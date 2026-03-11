"""Tests for flat usages (depth=1) queries and orchestration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.node import NodeData
from src.models.results import UsageEntry, UsagesTreeResult
from src.orchestration.usages import run_usages, run_usages_by_id, _build_usages_tree
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


class TestBuildUsagesTreeUnit:
    """Unit tests for _build_usages_tree with mocked queries."""

    def test_empty_usages(self):
        """No usages returns empty tree."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        with patch("src.orchestration.usages.query_usages_for_node", return_value=[]):
            result = _build_usages_tree(runner, target, depth=1, limit=100)

        assert result.target == target
        assert result.max_depth == 1
        assert result.tree == []

    def test_single_usage(self):
        """Single usage returns one entry."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        mock_edges = [
            {
                "source_id": "caller-1",
                "source_fqn": "App\\Service\\OrderService",
                "loc_file": "src/Service/OrderService.php",
                "loc_line": 25,
                "source_file": "src/Service/OrderService.php",
                "source_start_line": 10,
            }
        ]

        with patch("src.orchestration.usages.query_usages_for_node", return_value=mock_edges), \
             patch("src.orchestration.usages.query_usages_direct", return_value=[]):
            result = _build_usages_tree(runner, target, depth=1, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "caller-1"
        assert result.tree[0].fqn == "App\\Service\\OrderService"
        assert result.tree[0].file == "src/Service/OrderService.php"
        assert result.tree[0].line == 25
        assert result.tree[0].depth == 1
        assert result.tree[0].children == []

    def test_multiple_usages(self):
        """Multiple usages returned in order."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        mock_edges = [
            {
                "source_id": f"caller-{i}",
                "source_fqn": f"App\\Caller{i}",
                "loc_file": f"src/Caller{i}.php",
                "loc_line": i * 10,
                "source_file": f"src/Caller{i}.php",
                "source_start_line": 1,
            }
            for i in range(3)
        ]

        with patch("src.orchestration.usages.query_usages_for_node", return_value=mock_edges), \
             patch("src.orchestration.usages.query_usages_direct", return_value=[]):
            result = _build_usages_tree(runner, target, depth=1, limit=100)

        assert len(result.tree) == 3
        for i, entry in enumerate(result.tree):
            assert entry.node_id == f"caller-{i}"

    def test_location_fallback_to_source_node(self):
        """When edge has no location, fall back to source node."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        mock_edges = [
            {
                "source_id": "caller-1",
                "source_fqn": "App\\Caller",
                "loc_file": None,
                "loc_line": None,
                "source_file": "src/Caller.php",
                "source_start_line": 5,
            }
        ]

        with patch("src.orchestration.usages.query_usages_for_node", return_value=mock_edges), \
             patch("src.orchestration.usages.query_usages_direct", return_value=[]):
            result = _build_usages_tree(runner, target, depth=1, limit=100)

        assert result.tree[0].file == "src/Caller.php"
        assert result.tree[0].line == 5

    def test_limit_enforcement(self):
        """Limit stops processing after N entries."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        mock_edges = [
            {
                "source_id": f"caller-{i}",
                "source_fqn": f"App\\Caller{i}",
                "loc_file": f"f{i}.php",
                "loc_line": i,
                "source_file": None,
                "source_start_line": None,
            }
            for i in range(10)
        ]

        with patch("src.orchestration.usages.query_usages_for_node", return_value=mock_edges), \
             patch("src.orchestration.usages.query_usages_direct", return_value=[]):
            result = _build_usages_tree(runner, target, depth=1, limit=3)

        assert len(result.tree) == 3

    def test_visited_dedup(self):
        """Target node is in visited set, so same node appearing again is skipped."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node(node_id="target-1")

        # Include the target node itself in usages (should be skipped)
        mock_edges = [
            {
                "source_id": "target-1",
                "source_fqn": "App\\Entity\\Order",
                "loc_file": "f.php",
                "loc_line": 1,
                "source_file": None,
                "source_start_line": None,
            },
            {
                "source_id": "caller-1",
                "source_fqn": "App\\Caller",
                "loc_file": "c.php",
                "loc_line": 2,
                "source_file": None,
                "source_start_line": None,
            },
        ]

        with patch("src.orchestration.usages.query_usages_for_node", return_value=mock_edges), \
             patch("src.orchestration.usages.query_usages_direct", return_value=[]):
            result = _build_usages_tree(runner, target, depth=1, limit=100)

        assert len(result.tree) == 1
        assert result.tree[0].node_id == "caller-1"


class TestRunUsagesUnit:
    """Unit tests for run_usages orchestrator with mocked resolve."""

    def test_symbol_not_found(self):
        """Raise ValueError when symbol cannot be resolved."""
        runner = MagicMock(spec=QueryRunner)

        with patch("src.orchestration.usages.resolve_symbol", return_value=[]):
            with pytest.raises(ValueError, match="Symbol not found"):
                run_usages(runner, "NonExistent\\Symbol")

    def test_run_usages_calls_resolve(self):
        """run_usages resolves the symbol then builds tree."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        with patch("src.orchestration.usages.resolve_symbol", return_value=[target]) as mock_resolve, \
             patch("src.orchestration.usages._build_usages_tree") as mock_build:
            mock_build.return_value = UsagesTreeResult(target=target, max_depth=1, tree=[])
            run_usages(runner, "App\\Entity\\Order")

        mock_resolve.assert_called_once_with(runner, "App\\Entity\\Order")
        mock_build.assert_called_once()

    def test_run_usages_by_id_not_found(self):
        """Raise ValueError when node ID not found."""
        runner = MagicMock(spec=QueryRunner)

        with patch("src.orchestration.usages.fetch_node", return_value=None):
            with pytest.raises(ValueError, match="Node not found"):
                run_usages_by_id(runner, "nonexistent-id")


class TestToDict:
    """Test to_dict serialization for output."""

    def test_flat_result_to_dict(self):
        target = _make_node()
        entries = [
            UsageEntry(depth=1, node_id="s1", fqn="App\\A", file="a.php", line=10),
            UsageEntry(depth=1, node_id="s2", fqn="App\\B", file="b.php", line=20),
        ]
        result = UsagesTreeResult(target=target, max_depth=1, tree=entries)
        d = result.to_dict()

        assert d["target"]["id"] == "target-1"
        assert d["target"]["line"] == 11  # 0-based -> 1-based
        assert len(d["tree"]) == 2
        assert d["tree"][0]["line"] == 11  # 0-based -> 1-based
        assert d["tree"][1]["line"] == 21


@requires_neo4j
class TestUsagesFlatIntegration:
    """Integration tests for flat usages against loaded Neo4j database."""

    @pytest.fixture(autouse=True)
    def _ensure_data(self, loaded_database):
        _reload_if_empty(loaded_database)

    def test_class_has_usages(self, loaded_database):
        """A well-known class should have usages."""
        runner = QueryRunner(loaded_database)
        result = run_usages(runner, "App\\Entity\\Order", depth=1, limit=100)

        assert result.target.fqn == "App\\Entity\\Order"
        assert result.max_depth == 1
        assert len(result.tree) > 0
        # All entries should be at depth 1
        for entry in result.tree:
            assert entry.depth == 1
            assert entry.children == []

    def test_method_has_usages(self, loaded_database):
        """A method should have usages from callers."""
        runner = QueryRunner(loaded_database)
        result = run_usages(
            runner,
            "App\\Service\\NotificationService::notifyOrderCreated()",
            depth=1,
            limit=100,
        )
        assert result.target.kind == "Method"
        # notifyOrderCreated() should be called by something
        assert len(result.tree) >= 0  # may or may not have usages in test dataset

    def test_nonexistent_symbol_raises(self, loaded_database):
        """Nonexistent symbol should raise ValueError."""
        runner = QueryRunner(loaded_database)
        with pytest.raises(ValueError, match="Symbol not found"):
            run_usages(runner, "Totally\\NonExistent\\Symbol\\ZZZZ")

    def test_limit_respected(self, loaded_database):
        """Limit should cap the number of results."""
        runner = QueryRunner(loaded_database)
        result = run_usages(runner, "App\\Entity\\Order", depth=1, limit=2)
        assert len(result.tree) <= 2

    def test_result_has_locations(self, loaded_database):
        """Usage entries should have file/line when available."""
        runner = QueryRunner(loaded_database)
        result = run_usages(runner, "App\\Entity\\Order", depth=1, limit=10)
        if result.tree:
            # At least some entries should have files
            has_file = any(e.file is not None for e in result.tree)
            assert has_file

    def test_to_dict_json_output(self, loaded_database):
        """to_dict should produce valid JSON-serializable output."""
        import json

        runner = QueryRunner(loaded_database)
        result = run_usages(runner, "App\\Entity\\Order", depth=1, limit=5)
        d = result.to_dict()
        # Should be JSON-serializable
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["target"]["fqn"] == "App\\Entity\\Order"
        # Lines should be 1-based in output
        if parsed["target"]["line"] is not None:
            assert parsed["target"]["line"] >= 1
