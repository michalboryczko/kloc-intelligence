"""Tests for overrides command (method override chain)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.node import NodeData
from src.models.results import OverrideEntry, OverridesTreeResult
from src.orchestration.simple import (
    run_overrides,
    run_overrides_by_id,
    _build_overrides_tree,
)
from src.db.query_runner import QueryRunner
from .conftest import requires_neo4j


def _make_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "method-1",
        "kind": "Method",
        "name": "process",
        "fqn": "App\\Service\\OrderService::process()",
        "symbol": "scip-php . . App/Service/OrderService#process().",
        "file": "src/Service/OrderService.php",
        "start_line": 30,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _mock_neighbor(node_id, fqn, file=None, start_line=None):
    return {
        "node_id": node_id,
        "fqn": fqn,
        "file": file,
        "start_line": start_line,
    }


def _reload_if_empty(conn):
    """Reload test data if the database was cleared by another test."""
    from src.db.schema import ensure_schema
    from src.db.importer import parse_sot, import_nodes, import_edges

    runner = QueryRunner(conn)
    count = runner.execute_count("MATCH (n:Node) RETURN count(n)")
    if count == 0:
        sot_path = (
            Path(__file__).parent.parent.parent
            / "artifacts" / "kloc-dev" / "context-final" / "sot.json"
        )
        ensure_schema(conn)
        nodes, edges = parse_sot(str(sot_path))
        import_nodes(conn, nodes)
        import_edges(conn, edges)


class TestOverridesTreeUnit:
    """Unit tests for overrides tree building with mocked queries."""

    def test_no_overrides(self):
        """A method with no overrides returns empty tree."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        with patch("src.orchestration.simple.query_override_neighbors", return_value=[]), \
             patch("src.orchestration.simple.fetch_node", return_value=target):
            result = _build_overrides_tree(runner, target, "up", depth=5, limit=100)

        assert result.root == target
        assert result.direction == "up"
        assert result.tree == []

    def test_single_override_up(self):
        """Method overrides one parent method."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()
        parent = _make_node(
            node_id="parent-m", fqn="App\\Base::process()",
            file="src/Base.php", start_line=15,
        )

        neighbors = [_mock_neighbor("parent-m", "App\\Base::process()", "src/Base.php", 15)]

        call_count = [0]

        def mock_neighbors(r, nid, direction):
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] == 1:
                return neighbors
            return []

        with patch("src.orchestration.simple.query_override_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", return_value=parent):
            result = _build_overrides_tree(runner, target, "up", depth=5, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "parent-m"
        assert result.tree[0].fqn == "App\\Base::process()"
        assert result.tree[0].depth == 1

    def test_single_override_down(self):
        """Method is overridden by one child method."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()
        child = _make_node(
            node_id="child-m", fqn="App\\Sub::process()",
            file="src/Sub.php", start_line=25,
        )

        neighbors = [_mock_neighbor("child-m", "App\\Sub::process()", "src/Sub.php", 25)]

        call_count = [0]

        def mock_neighbors(r, nid, direction):
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] == 1:
                return neighbors
            return []

        with patch("src.orchestration.simple.query_override_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", return_value=child):
            result = _build_overrides_tree(runner, target, "down", depth=5, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "child-m"
        assert result.direction == "down"

    def test_two_level_chain(self):
        """A -> B -> C override chain with depth=2."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A::foo()")
        node_b = _make_node(node_id="B", fqn="B::foo()", start_line=20)
        node_c = _make_node(node_id="C", fqn="C::foo()", start_line=30)

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return [_mock_neighbor("B", "B::foo()")]
            elif nid == "B":
                return [_mock_neighbor("C", "C::foo()")]
            return []

        def mock_fetch(r, nid):
            if nid == "B":
                return node_b
            elif nid == "C":
                return node_c
            return None

        with patch("src.orchestration.simple.query_override_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", side_effect=mock_fetch):
            result = _build_overrides_tree(runner, target, "up", depth=5, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "B"
        assert result.tree[0].depth == 1
        assert len(result.tree[0].children) == 1
        assert result.tree[0].children[0].node_id == "C"
        assert result.tree[0].children[0].depth == 2

    def test_visited_prevents_cycle(self):
        """Cycles in override graph should not cause infinite loop."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A::foo()")
        node_b = _make_node(node_id="B", fqn="B::foo()")

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return [_mock_neighbor("B", "B::foo()")]
            elif nid == "B":
                return [_mock_neighbor("A", "A::foo()")]  # Cycle
            return []

        with patch("src.orchestration.simple.query_override_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", return_value=node_b):
            result = _build_overrides_tree(runner, target, "up", depth=10, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].children == []

    def test_depth_limit(self):
        """Depth limit stops BFS expansion."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A::foo()")
        node_b = _make_node(node_id="B", fqn="B::foo()")

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return [_mock_neighbor("B", "B::foo()")]
            elif nid == "B":
                return [_mock_neighbor("C", "C::foo()")]
            return []

        with patch("src.orchestration.simple.query_override_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", return_value=node_b):
            result = _build_overrides_tree(runner, target, "up", depth=1, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].children == []

    def test_count_limit(self):
        """Count limit stops BFS."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A::foo()")

        neighbors = [
            _mock_neighbor("B", "B::foo()"),
            _mock_neighbor("C", "C::foo()"),
            _mock_neighbor("D", "D::foo()"),
        ]

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return neighbors
            return []

        def mock_fetch(r, nid):
            return _make_node(node_id=nid, fqn=f"{nid}::foo()")

        with patch("src.orchestration.simple.query_override_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", side_effect=mock_fetch):
            result = _build_overrides_tree(runner, target, "down", depth=5, limit=2)

        assert len(result.tree) <= 2

    def test_multiple_children(self):
        """A method overridden by multiple children gets all entries."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A::foo()")

        neighbors = [
            _mock_neighbor("B", "B::foo()"),
            _mock_neighbor("C", "C::foo()"),
        ]

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return neighbors
            return []

        def mock_fetch(r, nid):
            return _make_node(node_id=nid, fqn=f"{nid}::foo()")

        with patch("src.orchestration.simple.query_override_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", side_effect=mock_fetch):
            result = _build_overrides_tree(runner, target, "down", depth=5, limit=100)

        assert len(result.tree) == 2
        node_ids = {e.node_id for e in result.tree}
        assert "B" in node_ids
        assert "C" in node_ids


class TestRunOverridesUnit:
    """Unit tests for run_overrides orchestrator with mocked resolve."""

    def test_symbol_not_found(self):
        runner = MagicMock(spec=QueryRunner)
        with patch("src.orchestration.simple.resolve_symbol", return_value=[]):
            with pytest.raises(ValueError, match="Symbol not found"):
                run_overrides(runner, "NonExistent\\Symbol")

    def test_wrong_kind_raises(self):
        """Overrides on a Class should raise ValueError."""
        runner = MagicMock(spec=QueryRunner)
        cls = _make_node(kind="Class")
        with patch("src.orchestration.simple.resolve_symbol", return_value=[cls]):
            with pytest.raises(ValueError, match="Node must be Method"):
                run_overrides(runner, "App\\SomeClass")

    def test_run_overrides_by_id_not_found(self):
        runner = MagicMock(spec=QueryRunner)
        with patch("src.orchestration.simple.fetch_node", return_value=None):
            with pytest.raises(ValueError, match="Node not found"):
                run_overrides_by_id(runner, "nonexistent-id")

    def test_run_overrides_by_id_wrong_kind(self):
        runner = MagicMock(spec=QueryRunner)
        cls = _make_node(kind="Class")
        with patch("src.orchestration.simple.fetch_node", return_value=cls):
            with pytest.raises(ValueError, match="Node must be Method"):
                run_overrides_by_id(runner, "class-1")


class TestOverridesToDict:
    """Test to_dict serialization for overrides output."""

    def test_flat_result(self):
        target = _make_node()
        entries = [
            OverrideEntry(depth=1, node_id="p1", fqn="Base::process()",
                          file="base.php", line=10),
        ]
        result = OverridesTreeResult(root=target, direction="up", max_depth=5, tree=entries)
        d = result.to_dict()
        assert d["direction"] == "up"
        assert d["max_depth"] == 5
        assert len(d["tree"]) == 1
        assert d["tree"][0]["line"] == 11  # 0-based to 1-based

    def test_nested_result(self):
        target = _make_node()
        child = OverrideEntry(depth=2, node_id="gp1", fqn="GrandBase::process()",
                              file="gb.php", line=5)
        parent = OverrideEntry(depth=1, node_id="p1", fqn="Base::process()",
                               children=[child])
        result = OverridesTreeResult(root=target, direction="up", max_depth=5, tree=[parent])
        d = result.to_dict()
        assert d["tree"][0]["children"][0]["fqn"] == "GrandBase::process()"
        assert d["tree"][0]["children"][0]["line"] == 6

    def test_root_serialization(self):
        target = _make_node(start_line=30)
        result = OverridesTreeResult(root=target, direction="down", max_depth=3, tree=[])
        d = result.to_dict()
        assert d["root"]["line"] == 31
        assert d["root"]["fqn"] == target.fqn

    def test_entry_optional_fields(self):
        """Entries without file/line omit those keys."""
        entry = OverrideEntry(depth=1, node_id="x", fqn="X::foo()")
        d = entry.to_dict()
        assert "file" not in d
        assert "line" not in d


def _count_entries(entries):
    """Count total entries in a tree."""
    total = 0
    for e in entries:
        total += 1
        total += _count_entries(e.children)
    return total


@requires_neo4j
class TestOverridesIntegration:
    """Integration tests for overrides against loaded Neo4j database."""

    @pytest.fixture(autouse=True)
    def _ensure_data(self, loaded_database):
        _reload_if_empty(loaded_database)

    def test_method_overrides_no_error(self, loaded_database):
        """Running overrides on a method should not raise errors."""
        runner = QueryRunner(loaded_database)
        # Find any method node first
        method_record = runner.execute_single(
            "MATCH (n:Node {kind: 'Method'}) RETURN n LIMIT 1"
        )
        if method_record is None:
            pytest.skip("No Method nodes in database")
        from src.db.result_mapper import record_to_node
        method = record_to_node(method_record)
        result = run_overrides_by_id(
            runner, method.node_id, direction="up", depth=3, limit=50
        )
        assert result.direction == "up"
        result_down = run_overrides_by_id(
            runner, method.node_id, direction="down", depth=3, limit=50
        )
        assert result_down.direction == "down"

    def test_class_raises_error(self, loaded_database):
        """Running overrides on a class should raise ValueError."""
        runner = QueryRunner(loaded_database)
        with pytest.raises(ValueError, match="Node must be Method"):
            run_overrides(runner, "App\\Entity\\Order")

    def test_no_duplicate_nodes(self, loaded_database):
        """No node should appear twice in the tree."""
        runner = QueryRunner(loaded_database)
        # Find a method that may have overrides
        method_record = runner.execute_single(
            "MATCH (n:Node {kind: 'Method'})-[:OVERRIDES]->() RETURN n LIMIT 1"
        )
        if method_record is None:
            pytest.skip("No methods with overrides in database")
        from src.db.result_mapper import record_to_node
        method = record_to_node(method_record)
        result = run_overrides_by_id(
            runner, method.node_id, direction="up", depth=5, limit=50
        )

        seen = set()

        def check_unique(entries):
            for e in entries:
                assert e.node_id not in seen, f"Duplicate: {e.node_id}"
                seen.add(e.node_id)
                check_unique(e.children)

        check_unique(result.tree)

    def test_nonexistent_symbol_raises(self, loaded_database):
        """Nonexistent symbol should raise ValueError."""
        runner = QueryRunner(loaded_database)
        with pytest.raises(ValueError, match="Symbol not found"):
            run_overrides(runner, "Totally\\NonExistent\\ZZZZ")

    def test_to_dict_json_output(self, loaded_database):
        """to_dict should produce valid JSON-serializable output."""
        import json

        runner = QueryRunner(loaded_database)
        method_record = runner.execute_single(
            "MATCH (n:Node {kind: 'Method'}) RETURN n LIMIT 1"
        )
        if method_record is None:
            pytest.skip("No Method nodes in database")
        from src.db.result_mapper import record_to_node
        method = record_to_node(method_record)
        result = run_overrides_by_id(
            runner, method.node_id, direction="up", depth=2, limit=10
        )
        d = result.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["root"]["fqn"] == method.fqn
        assert parsed["direction"] == "up"
