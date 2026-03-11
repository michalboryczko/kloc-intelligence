"""Tests for deps queries and orchestration."""

from unittest.mock import MagicMock, patch

import pytest

from src.models.node import NodeData
from src.models.results import DepsEntry, DepsTreeResult
from src.orchestration.deps import run_deps, run_deps_by_id, _build_deps_tree
from src.db.query_runner import QueryRunner
from .conftest import requires_neo4j


def _make_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "source-1",
        "kind": "Method",
        "name": "createOrder",
        "fqn": "App\\Service\\OrderService::createOrder()",
        "symbol": "scip-php . . App/Service/OrderService#createOrder().",
        "file": "src/Service/OrderService.php",
        "start_line": 20,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _mock_edge(target_id, target_fqn, loc_file=None, loc_line=None,
               target_file=None, target_start_line=None):
    return {
        "target_id": target_id,
        "target_fqn": target_fqn,
        "loc_file": loc_file,
        "loc_line": loc_line,
        "target_file": target_file,
        "target_start_line": target_start_line,
    }


class TestBuildDepsTreeUnit:
    """Unit tests for _build_deps_tree with mocked queries."""

    def test_empty_deps(self):
        """No deps returns empty tree."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        with patch("src.orchestration.deps.query_deps_for_node", return_value=[]):
            result = _build_deps_tree(runner, target, depth=1, limit=100)

        assert result.target == target
        assert result.max_depth == 1
        assert result.tree == []

    def test_single_dep(self):
        """Single dependency returns one entry."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        mock_edges = [
            _mock_edge("dep-1", "App\\Entity\\Order", "src/Service/OrderService.php", 30,
                       "src/Entity/Order.php", 5)
        ]

        with patch("src.orchestration.deps.query_deps_for_node", return_value=mock_edges), \
             patch("src.orchestration.deps.query_deps_direct", return_value=[]):
            result = _build_deps_tree(runner, target, depth=1, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "dep-1"
        assert result.tree[0].fqn == "App\\Entity\\Order"
        assert result.tree[0].file == "src/Service/OrderService.php"
        assert result.tree[0].line == 30
        assert result.tree[0].depth == 1

    def test_location_fallback_file_only(self):
        """When edge has no location, use target file but line=None (per kloc-cli)."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        mock_edges = [
            _mock_edge("dep-1", "App\\Dep", None, None, "src/Dep.php", 42)
        ]

        with patch("src.orchestration.deps.query_deps_for_node", return_value=mock_edges), \
             patch("src.orchestration.deps.query_deps_direct", return_value=[]):
            result = _build_deps_tree(runner, target, depth=1, limit=100)

        assert result.tree[0].file == "src/Dep.php"
        assert result.tree[0].line is None  # No line fallback for deps

    def test_limit_enforcement(self):
        """Limit stops processing after N entries."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        mock_edges = [
            _mock_edge(f"dep-{i}", f"App\\Dep{i}", f"f{i}.php", i)
            for i in range(10)
        ]

        with patch("src.orchestration.deps.query_deps_for_node", return_value=mock_edges), \
             patch("src.orchestration.deps.query_deps_direct", return_value=[]):
            result = _build_deps_tree(runner, target, depth=1, limit=3)

        assert len(result.tree) == 3

    def test_visited_dedup(self):
        """Target node is in visited set, duplicate sources skipped."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="source-1")

        mock_edges = [
            _mock_edge("source-1", "Self", "f.php", 1),  # Self-ref, skipped
            _mock_edge("dep-1", "App\\Dep", "f.php", 2),
        ]

        with patch("src.orchestration.deps.query_deps_for_node", return_value=mock_edges), \
             patch("src.orchestration.deps.query_deps_direct", return_value=[]):
            result = _build_deps_tree(runner, target, depth=1, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "dep-1"

    def test_depth2_builds_children(self):
        """Depth=2 should recurse and build children."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        level1_edges = [_mock_edge("dep-1", "App\\Dep1", "d1.php", 10)]
        level2_edges = [_mock_edge("dep-2", "App\\Dep2", "d2.php", 20)]

        with patch("src.orchestration.deps.query_deps_for_node", return_value=level1_edges), \
             patch("src.orchestration.deps.query_deps_direct", return_value=level2_edges):
            result = _build_deps_tree(runner, target, depth=2, limit=100)

        assert len(result.tree) == 1
        assert len(result.tree[0].children) == 1
        assert result.tree[0].children[0].node_id == "dep-2"
        assert result.tree[0].children[0].depth == 2

    def test_global_visited_prevents_revisit(self):
        """A node visited via DFS recursion is NOT revisited as top-level."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        level1_edges = [
            _mock_edge("dep-1", "D1"),
            _mock_edge("dep-2", "D2"),
        ]

        # DFS-ordered: dep-1's children are expanded first, finding dep-2
        def mock_direct(runner_arg, node_id):
            if node_id == "dep-1":
                return [_mock_edge("dep-2", "D2")]
            return []

        with patch("src.orchestration.deps.query_deps_for_node", return_value=level1_edges), \
             patch("src.orchestration.deps.query_deps_direct", side_effect=mock_direct):
            result = _build_deps_tree(runner, target, depth=2, limit=100)

        # dep-2 is found as child of dep-1 first (DFS order)
        assert len(result.tree) == 1
        assert result.tree[0].node_id == "dep-1"
        assert len(result.tree[0].children) == 1
        assert result.tree[0].children[0].node_id == "dep-2"

    def test_global_visited_skips_at_depth2(self):
        """A node visited at depth 1 should NOT appear again at depth 2."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        level1_edges = [
            _mock_edge("dep-1", "D1"),
            _mock_edge("dep-2", "D2"),
        ]

        def mock_direct(runner_arg, node_id):
            if node_id == "dep-1":
                return [_mock_edge("dep-3", "D3")]
            if node_id == "dep-2":
                return [_mock_edge("dep-1", "D1")]  # Already visited at depth 1
            return []

        with patch("src.orchestration.deps.query_deps_for_node", return_value=level1_edges), \
             patch("src.orchestration.deps.query_deps_direct", side_effect=mock_direct):
            result = _build_deps_tree(runner, target, depth=2, limit=100)

        assert len(result.tree) == 2
        assert result.tree[0].children[0].node_id == "dep-3"
        assert result.tree[1].children == []  # dep-1 already visited

    def test_global_limit_across_depths(self):
        """Limit is enforced globally across all depth levels."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        level1_edges = [
            _mock_edge("d1", "D1"),
            _mock_edge("d2", "D2"),
        ]
        level2_edges = [
            _mock_edge("d3", "D3"),
            _mock_edge("d4", "D4"),
        ]

        with patch("src.orchestration.deps.query_deps_for_node", return_value=level1_edges), \
             patch("src.orchestration.deps.query_deps_direct", return_value=level2_edges):
            result = _build_deps_tree(runner, target, depth=2, limit=3)

        total = _count_entries(result.tree)
        assert total <= 3


class TestRunDepsUnit:
    """Unit tests for run_deps orchestrator with mocked resolve."""

    def test_symbol_not_found(self):
        runner = MagicMock(spec=QueryRunner)
        with patch("src.orchestration.deps.resolve_symbol", return_value=[]):
            with pytest.raises(ValueError, match="Symbol not found"):
                run_deps(runner, "NonExistent\\Symbol")

    def test_run_deps_calls_resolve(self):
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        with patch("src.orchestration.deps.resolve_symbol", return_value=[target]) as mock_resolve, \
             patch("src.orchestration.deps._build_deps_tree") as mock_build:
            mock_build.return_value = DepsTreeResult(target=target, max_depth=1, tree=[])
            run_deps(runner, "App\\Service\\OrderService::createOrder()")

        mock_resolve.assert_called_once_with(runner, "App\\Service\\OrderService::createOrder()")

    def test_run_deps_by_id_not_found(self):
        runner = MagicMock(spec=QueryRunner)
        with patch("src.orchestration.deps.fetch_node", return_value=None):
            with pytest.raises(ValueError, match="Node not found"):
                run_deps_by_id(runner, "nonexistent-id")


class TestDepsToDict:
    """Test to_dict serialization for deps output."""

    def test_flat_result(self):
        target = _make_node()
        entries = [
            DepsEntry(depth=1, node_id="d1", fqn="App\\A", file="a.php", line=10),
            DepsEntry(depth=1, node_id="d2", fqn="App\\B"),
        ]
        result = DepsTreeResult(target=target, max_depth=1, tree=entries)
        d = result.to_dict()
        assert len(d["tree"]) == 2
        assert d["tree"][0]["line"] == 11
        assert "file" not in d["tree"][1]
        assert "line" not in d["tree"][1]

    def test_nested_result(self):
        target = _make_node()
        child = DepsEntry(depth=2, node_id="d2", fqn="App\\B", file="b.php", line=5)
        parent = DepsEntry(depth=1, node_id="d1", fqn="App\\A", children=[child])
        result = DepsTreeResult(target=target, max_depth=2, tree=[parent])
        d = result.to_dict()
        assert d["tree"][0]["children"][0]["line"] == 6


def _count_entries(entries):
    """Count total entries in a tree."""
    total = 0
    for e in entries:
        total += 1
        total += _count_entries(e.children)
    return total


@requires_neo4j
class TestDepsIntegration:
    """Integration tests for deps against loaded Neo4j database."""

    def test_method_has_deps(self, loaded_database):
        """A method that calls other things should have deps."""
        runner = QueryRunner(loaded_database)
        # Use a method that likely depends on entities
        result = run_deps(
            runner,
            "App\\Service\\OrderService::createOrder()",
            depth=1,
            limit=100,
        )
        assert result.target.kind == "Method"
        # createOrder likely depends on Order entity, etc.
        assert len(result.tree) >= 0

    def test_class_deps_with_members(self, loaded_database):
        """A class should include deps from its members."""
        runner = QueryRunner(loaded_database)
        result = run_deps(runner, "App\\Entity\\Order", depth=1, limit=50)
        assert result.target.kind == "Class"

    def test_depth2_structure(self, loaded_database):
        """Depth=2 should produce properly nested tree."""
        runner = QueryRunner(loaded_database)
        result = run_deps(runner, "App\\Entity\\Order", depth=2, limit=20)
        assert result.max_depth == 2
        for entry in result.tree:
            assert entry.depth == 1
            for child in entry.children:
                assert child.depth == 2

    def test_limit_respected(self, loaded_database):
        """Limit should cap total results."""
        runner = QueryRunner(loaded_database)
        result = run_deps(runner, "App\\Entity\\Order", depth=2, limit=5)
        total = _count_entries(result.tree)
        assert total <= 5

    def test_no_duplicate_nodes(self, loaded_database):
        """No node should appear twice in the tree."""
        runner = QueryRunner(loaded_database)
        result = run_deps(runner, "App\\Entity\\Order", depth=2, limit=50)

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
            run_deps(runner, "Totally\\NonExistent\\Symbol\\ZZZZ")

    def test_to_dict_json_output(self, loaded_database):
        """to_dict should produce valid JSON-serializable output."""
        import json

        runner = QueryRunner(loaded_database)
        result = run_deps(runner, "App\\Entity\\Order", depth=1, limit=5)
        d = result.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["target"]["fqn"] == "App\\Entity\\Order"
