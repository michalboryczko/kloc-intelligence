"""Tests for inherit command (inheritance tree traversal)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.node import NodeData
from src.models.results import InheritEntry, InheritTreeResult
from src.orchestration.simple import (
    run_inherit,
    run_inherit_by_id,
    _build_inherit_tree,
)
from src.db.query_runner import QueryRunner
from .conftest import requires_neo4j


def _make_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "class-1",
        "kind": "Class",
        "name": "Order",
        "fqn": "App\\Entity\\Order",
        "symbol": "scip-php . . App/Entity/Order#",
        "file": "src/Entity/Order.php",
        "start_line": 10,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _mock_neighbor(node_id, fqn, kind="Class", file=None, start_line=None):
    return {
        "node_id": node_id,
        "fqn": fqn,
        "kind": kind,
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


class TestInheritTreeUnit:
    """Unit tests for inherit tree building with mocked queries."""

    def test_no_parents(self):
        """A class with no parents returns empty tree."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        with patch("src.orchestration.simple.query_inherit_neighbors", return_value=[]), \
             patch("src.orchestration.simple.fetch_node", return_value=target):
            result = _build_inherit_tree(runner, target, "up", depth=5, limit=100)

        assert result.root == target
        assert result.direction == "up"
        assert result.tree == []

    def test_single_parent(self):
        """A class extending one parent returns one entry."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()
        parent = _make_node(
            node_id="parent-1", kind="Class", fqn="App\\BaseEntity",
            file="src/BaseEntity.php", start_line=5,
        )

        neighbors = [_mock_neighbor("parent-1", "App\\BaseEntity", "Class", "src/BaseEntity.php", 5)]

        call_count = [0]

        def mock_neighbors(r, nid, direction):
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] == 1:
                return neighbors  # First call: target's neighbors
            return []  # Second call: parent's neighbors (none)

        with patch("src.orchestration.simple.query_inherit_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", return_value=parent):
            result = _build_inherit_tree(runner, target, "up", depth=5, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "parent-1"
        assert result.tree[0].fqn == "App\\BaseEntity"
        assert result.tree[0].depth == 1

    def test_two_level_chain(self):
        """A -> B -> C chain with depth=2."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A")
        node_b = _make_node(node_id="B", fqn="B", start_line=20)
        node_c = _make_node(node_id="C", fqn="C", start_line=30)

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return [_mock_neighbor("B", "B")]
            elif nid == "B":
                return [_mock_neighbor("C", "C")]
            return []

        def mock_fetch(r, nid):
            if nid == "B":
                return node_b
            elif nid == "C":
                return node_c
            return None

        with patch("src.orchestration.simple.query_inherit_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", side_effect=mock_fetch):
            result = _build_inherit_tree(runner, target, "up", depth=5, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "B"
        assert result.tree[0].depth == 1
        assert len(result.tree[0].children) == 1
        assert result.tree[0].children[0].node_id == "C"
        assert result.tree[0].children[0].depth == 2

    def test_visited_prevents_cycle(self):
        """Cycles in inheritance graph should not cause infinite loop."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A")
        node_b = _make_node(node_id="B", fqn="B")

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return [_mock_neighbor("B", "B")]
            elif nid == "B":
                return [_mock_neighbor("A", "A")]  # Cycle back to A
            return []

        with patch("src.orchestration.simple.query_inherit_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", return_value=node_b):
            result = _build_inherit_tree(runner, target, "up", depth=10, limit=100)

        # Only B should appear (A is already visited as root)
        assert len(result.tree) == 1
        assert result.tree[0].node_id == "B"
        assert result.tree[0].children == []

    def test_depth_limit(self):
        """Depth limit stops BFS expansion."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A")
        node_b = _make_node(node_id="B", fqn="B")

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return [_mock_neighbor("B", "B")]
            elif nid == "B":
                return [_mock_neighbor("C", "C")]  # Would be depth 2
            return []

        with patch("src.orchestration.simple.query_inherit_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", return_value=node_b):
            result = _build_inherit_tree(runner, target, "up", depth=1, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].children == []  # No depth-2 expansion

    def test_count_limit(self):
        """Count limit stops BFS."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A")

        neighbors = [
            _mock_neighbor("B", "B"),
            _mock_neighbor("C", "C"),
            _mock_neighbor("D", "D"),
        ]

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return neighbors
            return []

        node_counter = [0]

        def mock_fetch(r, nid):
            node_counter[0] += 1
            return _make_node(node_id=nid, fqn=nid)

        with patch("src.orchestration.simple.query_inherit_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", side_effect=mock_fetch):
            result = _build_inherit_tree(runner, target, "up", depth=5, limit=2)

        assert len(result.tree) <= 2

    def test_multiple_parents(self):
        """A class implementing multiple interfaces gets all as entries."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="A", fqn="A")

        neighbors = [
            _mock_neighbor("I1", "Interface1", "Interface"),
            _mock_neighbor("I2", "Interface2", "Interface"),
        ]

        def mock_neighbors(r, nid, direction):
            if nid == "A":
                return neighbors
            return []

        fetch_count = [0]

        def mock_fetch(r, nid):
            fetch_count[0] += 1
            return _make_node(node_id=nid, fqn=nid, kind="Interface")

        with patch("src.orchestration.simple.query_inherit_neighbors", side_effect=mock_neighbors), \
             patch("src.orchestration.simple.fetch_node", side_effect=mock_fetch):
            result = _build_inherit_tree(runner, target, "up", depth=5, limit=100)

        assert len(result.tree) == 2
        node_ids = {e.node_id for e in result.tree}
        assert "I1" in node_ids
        assert "I2" in node_ids


class TestRunInheritUnit:
    """Unit tests for run_inherit orchestrator with mocked resolve."""

    def test_symbol_not_found(self):
        runner = MagicMock(spec=QueryRunner)
        with patch("src.orchestration.simple.resolve_symbol", return_value=[]):
            with pytest.raises(ValueError, match="Symbol not found"):
                run_inherit(runner, "NonExistent\\Symbol")

    def test_wrong_kind_raises(self):
        """Inherit on a Method should raise ValueError."""
        runner = MagicMock(spec=QueryRunner)
        method = _make_node(kind="Method")
        with patch("src.orchestration.simple.resolve_symbol", return_value=[method]):
            with pytest.raises(ValueError, match="Node must be Class/Interface/Trait/Enum"):
                run_inherit(runner, "App\\SomeMethod")

    def test_run_inherit_by_id_not_found(self):
        runner = MagicMock(spec=QueryRunner)
        with patch("src.orchestration.simple.fetch_node", return_value=None):
            with pytest.raises(ValueError, match="Node not found"):
                run_inherit_by_id(runner, "nonexistent-id")

    def test_run_inherit_by_id_wrong_kind(self):
        runner = MagicMock(spec=QueryRunner)
        method = _make_node(kind="Method")
        with patch("src.orchestration.simple.fetch_node", return_value=method):
            with pytest.raises(ValueError, match="Node must be Class/Interface/Trait/Enum"):
                run_inherit_by_id(runner, "method-1")


class TestInheritToDict:
    """Test to_dict serialization for inherit output."""

    def test_flat_result(self):
        target = _make_node()
        entries = [
            InheritEntry(depth=1, node_id="p1", fqn="Parent", kind="Class",
                         file="p.php", line=10),
        ]
        result = InheritTreeResult(root=target, direction="up", max_depth=5, tree=entries)
        d = result.to_dict()
        assert d["direction"] == "up"
        assert d["max_depth"] == 5
        assert len(d["tree"]) == 1
        assert d["tree"][0]["line"] == 11  # 0-based to 1-based
        assert d["tree"][0]["kind"] == "Class"

    def test_nested_result(self):
        target = _make_node()
        child = InheritEntry(depth=2, node_id="gp1", fqn="GrandParent", kind="Class",
                             file="gp.php", line=5)
        parent = InheritEntry(depth=1, node_id="p1", fqn="Parent", kind="Class",
                              children=[child])
        result = InheritTreeResult(root=target, direction="up", max_depth=5, tree=[parent])
        d = result.to_dict()
        assert d["tree"][0]["children"][0]["fqn"] == "GrandParent"
        assert d["tree"][0]["children"][0]["line"] == 6

    def test_root_serialization(self):
        target = _make_node(start_line=10)
        result = InheritTreeResult(root=target, direction="down", max_depth=3, tree=[])
        d = result.to_dict()
        assert d["root"]["line"] == 11
        assert d["root"]["fqn"] == target.fqn

    def test_entry_optional_fields(self):
        """Entries without file/line omit those keys."""
        entry = InheritEntry(depth=1, node_id="x", fqn="X", kind="Interface")
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
class TestInheritIntegration:
    """Integration tests for inherit against loaded Neo4j database."""

    @pytest.fixture(autouse=True)
    def _ensure_data(self, loaded_database):
        _reload_if_empty(loaded_database)

    def test_class_has_no_errors(self, loaded_database):
        """Running inherit on a class should not raise errors."""
        runner = QueryRunner(loaded_database)
        # Try both directions; either may have results or not
        result_up = run_inherit(runner, "App\\Entity\\Order", direction="up", depth=3, limit=50)
        assert result_up.direction == "up"
        result_down = run_inherit(runner, "App\\Entity\\Order", direction="down", depth=3, limit=50)
        assert result_down.direction == "down"

    def test_tree_structure_valid(self, loaded_database):
        """Tree entries should have correct depth values."""
        runner = QueryRunner(loaded_database)
        result = run_inherit(runner, "App\\Entity\\Order", direction="up", depth=3, limit=50)
        for entry in result.tree:
            assert entry.depth == 1
            for child in entry.children:
                assert child.depth == 2

    def test_no_duplicate_nodes(self, loaded_database):
        """No node should appear twice in the tree."""
        runner = QueryRunner(loaded_database)
        result = run_inherit(runner, "App\\Entity\\Order", direction="up", depth=3, limit=50)

        seen = set()

        def check_unique(entries):
            for e in entries:
                assert e.node_id not in seen, f"Duplicate: {e.node_id}"
                seen.add(e.node_id)
                check_unique(e.children)

        check_unique(result.tree)

    def test_limit_respected(self, loaded_database):
        """Limit should cap total results."""
        runner = QueryRunner(loaded_database)
        result = run_inherit(runner, "App\\Entity\\Order", direction="up", depth=5, limit=3)
        total = _count_entries(result.tree)
        assert total <= 3

    def test_nonexistent_symbol_raises(self, loaded_database):
        """Nonexistent symbol should raise ValueError."""
        runner = QueryRunner(loaded_database)
        with pytest.raises(ValueError, match="Symbol not found"):
            run_inherit(runner, "Totally\\NonExistent\\ZZZZ")

    def test_to_dict_json_output(self, loaded_database):
        """to_dict should produce valid JSON-serializable output."""
        import json

        runner = QueryRunner(loaded_database)
        result = run_inherit(runner, "App\\Entity\\Order", direction="up", depth=2, limit=10)
        d = result.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["root"]["fqn"] == "App\\Entity\\Order"
        assert parsed["direction"] == "up"
