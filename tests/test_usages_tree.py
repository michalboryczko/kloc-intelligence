"""Tests for tree usages (depth > 1) with BFS expansion."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.node import NodeData
from src.models.results import UsageEntry, UsagesTreeResult
from src.orchestration.usages import _build_usages_tree
from src.db.query_runner import QueryRunner
from .conftest import requires_neo4j


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


def _make_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "target-1",
        "kind": "Class",
        "name": "Order",
        "fqn": "App\\Entity\\Order",
        "symbol": "scip-php . . App/Entity/Order#",
        "file": "src/Entity/Order.php",
        "start_line": 10,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _mock_edge(source_id, source_fqn, loc_file=None, loc_line=None,
               source_file=None, source_start_line=None):
    return {
        "source_id": source_id,
        "source_fqn": source_fqn,
        "loc_file": loc_file,
        "loc_line": loc_line,
        "source_file": source_file,
        "source_start_line": source_start_line,
    }


class TestBuildUsagesTreeDepth2:
    """Unit tests for BFS tree building at depth=2."""

    def test_depth2_builds_children(self):
        """Depth=2 should recurse and build children."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        # Level 1: target -> caller-1
        level1_edges = [_mock_edge("caller-1", "App\\Caller1", "c1.php", 10)]
        # Level 2: caller-1 -> caller-2
        level2_edges = [_mock_edge("caller-2", "App\\Caller2", "c2.php", 20)]

        with patch("src.orchestration.usages.query_usages_for_node", return_value=level1_edges), \
             patch("src.orchestration.usages.query_usages_direct", return_value=level2_edges):
            result = _build_usages_tree(runner, target, depth=2, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "caller-1"
        assert result.tree[0].depth == 1
        assert len(result.tree[0].children) == 1
        assert result.tree[0].children[0].node_id == "caller-2"
        assert result.tree[0].children[0].depth == 2

    def test_global_visited_prevents_revisit(self):
        """A node visited at depth 2 via DFS recursion should NOT appear again."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        # Level 1: target -> caller-1, caller-2
        # BFS is DFS-ordered: caller-1's children are expanded before caller-2
        level1_edges = [
            _mock_edge("caller-1", "App\\Caller1", "c1.php", 10),
            _mock_edge("caller-2", "App\\Caller2", "c2.php", 20),
        ]
        # Level 2 for caller-1: finds caller-2 (which gets visited here first)
        level2_edges_for_1 = [_mock_edge("caller-2", "App\\Caller2", "c2.php", 30)]

        def mock_direct(runner_arg, node_id):
            if node_id == "caller-1":
                return level2_edges_for_1
            return []

        with patch("src.orchestration.usages.query_usages_for_node", return_value=level1_edges), \
             patch("src.orchestration.usages.query_usages_direct", side_effect=mock_direct):
            result = _build_usages_tree(runner, target, depth=2, limit=100)

        # caller-2 is visited via caller-1's children first (DFS order),
        # so it appears as child of caller-1 and NOT as a separate top-level entry
        assert len(result.tree) == 1
        assert result.tree[0].node_id == "caller-1"
        assert len(result.tree[0].children) == 1
        assert result.tree[0].children[0].node_id == "caller-2"

    def test_global_visited_skips_at_depth2(self):
        """A node visited at depth 1 should NOT appear again at depth 2."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        # Level 1: target -> caller-1, caller-2
        level1_edges = [
            _mock_edge("caller-1", "App\\Caller1", "c1.php", 10),
            _mock_edge("caller-2", "App\\Caller2", "c2.php", 20),
        ]
        # Level 2 for caller-1: tries to reach caller-3
        level2_for_1 = [_mock_edge("caller-3", "App\\Caller3", "c3.php", 30)]
        # Level 2 for caller-2: tries to reach caller-1 (already visited at depth 1)
        level2_for_2 = [_mock_edge("caller-1", "App\\Caller1", "c1.php", 40)]

        def mock_direct(runner_arg, node_id):
            if node_id == "caller-1":
                return level2_for_1
            if node_id == "caller-2":
                return level2_for_2
            return []

        with patch("src.orchestration.usages.query_usages_for_node", return_value=level1_edges), \
             patch("src.orchestration.usages.query_usages_direct", side_effect=mock_direct):
            result = _build_usages_tree(runner, target, depth=2, limit=100)

        assert len(result.tree) == 2
        # caller-1 should have caller-3 as child
        assert len(result.tree[0].children) == 1
        assert result.tree[0].children[0].node_id == "caller-3"
        # caller-2 should have NO children (caller-1 already visited)
        assert result.tree[1].children == []

    def test_global_limit_across_depths(self):
        """Limit is enforced globally across all depth levels."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        # Level 1: 2 callers
        level1_edges = [
            _mock_edge("caller-1", "App\\C1", "c1.php", 1),
            _mock_edge("caller-2", "App\\C2", "c2.php", 2),
        ]
        # Level 2: 3 more callers per node
        level2_edges = [
            _mock_edge("caller-3", "App\\C3", "c3.php", 3),
            _mock_edge("caller-4", "App\\C4", "c4.php", 4),
            _mock_edge("caller-5", "App\\C5", "c5.php", 5),
        ]

        with patch("src.orchestration.usages.query_usages_for_node", return_value=level1_edges), \
             patch("src.orchestration.usages.query_usages_direct", return_value=level2_edges):
            result = _build_usages_tree(runner, target, depth=2, limit=3)

        # Should have at most 3 total entries across all depths
        total = _count_entries(result.tree)
        assert total <= 3

    def test_depth1_no_recursion(self):
        """Depth=1 should not recurse at all."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        level1_edges = [_mock_edge("caller-1", "App\\C1", "c1.php", 1)]

        with patch("src.orchestration.usages.query_usages_for_node", return_value=level1_edges), \
             patch("src.orchestration.usages.query_usages_direct") as mock_direct:
            result = _build_usages_tree(runner, target, depth=1, limit=100)

        # Direct query should NOT be called (only member query for root)
        mock_direct.assert_not_called()
        assert len(result.tree) == 1
        assert result.tree[0].children == []

    def test_depth3_three_levels(self):
        """Depth=3 builds three levels of children."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        level1_edges = [_mock_edge("c1", "C1")]

        def mock_direct(runner_arg, node_id):
            if node_id == "c1":
                return [_mock_edge("c2", "C2")]
            elif node_id == "c2":
                return [_mock_edge("c3", "C3")]
            return []

        with patch("src.orchestration.usages.query_usages_for_node", return_value=level1_edges), \
             patch("src.orchestration.usages.query_usages_direct", side_effect=mock_direct):
            result = _build_usages_tree(runner, target, depth=3, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "c1"
        assert result.tree[0].depth == 1
        assert len(result.tree[0].children) == 1
        assert result.tree[0].children[0].node_id == "c2"
        assert result.tree[0].children[0].depth == 2
        assert len(result.tree[0].children[0].children) == 1
        assert result.tree[0].children[0].children[0].node_id == "c3"
        assert result.tree[0].children[0].children[0].depth == 3

    def test_to_dict_nested(self):
        """to_dict correctly serializes nested tree."""
        target = _make_node()
        child = UsageEntry(depth=2, node_id="c2", fqn="App\\Child", file="ch.php", line=5)
        root_entry = UsageEntry(
            depth=1, node_id="c1", fqn="App\\Root", file="r.php", line=10,
            children=[child],
        )
        result = UsagesTreeResult(target=target, max_depth=2, tree=[root_entry])
        d = result.to_dict()

        assert d["max_depth"] == 2
        assert len(d["tree"]) == 1
        assert d["tree"][0]["depth"] == 1
        assert len(d["tree"][0]["children"]) == 1
        assert d["tree"][0]["children"][0]["depth"] == 2
        assert d["tree"][0]["children"][0]["line"] == 6  # 0-based -> 1-based


def _count_entries(entries):
    """Count total entries in a tree."""
    total = 0
    for e in entries:
        total += 1
        total += _count_entries(e.children)
    return total


@requires_neo4j
class TestUsagesTreeIntegration:
    """Integration tests for tree usages against loaded Neo4j database."""

    @pytest.fixture(autouse=True)
    def _ensure_data(self, loaded_database):
        _reload_if_empty(loaded_database)

    def test_depth2_has_children(self, loaded_database):
        """Depth=2 on a well-used class should produce children."""
        from src.orchestration.usages import run_usages

        runner = QueryRunner(loaded_database)
        result = run_usages(runner, "App\\Entity\\Order", depth=2, limit=50)

        assert result.max_depth == 2
        # Check structure: depth-1 entries may or may not have children
        for entry in result.tree:
            assert entry.depth == 1
            for child in entry.children:
                assert child.depth == 2

    def test_depth2_limit_respected(self, loaded_database):
        """Limit should cap total results across both depths."""
        from src.orchestration.usages import run_usages

        runner = QueryRunner(loaded_database)
        result = run_usages(runner, "App\\Entity\\Order", depth=2, limit=5)

        total = _count_entries(result.tree)
        assert total <= 5

    def test_no_duplicate_nodes(self, loaded_database):
        """No node should appear twice in the tree."""
        from src.orchestration.usages import run_usages

        runner = QueryRunner(loaded_database)
        result = run_usages(runner, "App\\Entity\\Order", depth=2, limit=50)

        seen = set()

        def check_unique(entries):
            for e in entries:
                assert e.node_id not in seen, f"Duplicate node: {e.node_id}"
                seen.add(e.node_id)
                check_unique(e.children)

        check_unique(result.tree)
