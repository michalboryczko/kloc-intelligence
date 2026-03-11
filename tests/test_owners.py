"""Tests for owners command (containment chain)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.node import NodeData
from src.models.results import OwnersResult
from src.orchestration.simple import run_owners, run_owners_by_id, _build_owners
from src.db.query_runner import QueryRunner
from .conftest import requires_neo4j


def _make_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "method-1",
        "kind": "Method",
        "name": "createOrder",
        "fqn": "App\\Service\\OrderService::createOrder()",
        "symbol": "scip-php . . App/Service/OrderService#createOrder().",
        "file": "src/Service/OrderService.php",
        "start_line": 20,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


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


class TestOwnersUnit:
    """Unit tests for owners with mocked queries."""

    def test_single_node_no_parent(self):
        """A File node has no parent — chain is just [File]."""
        file_node = _make_node(
            node_id="file-1", kind="File", name="Order.php",
            fqn="src/Entity/Order.php",
        )

        with patch("src.orchestration.simple.get_owners_chain", return_value=[file_node]):
            result = _build_owners(MagicMock(spec=QueryRunner), "file-1")

        assert len(result.chain) == 1
        assert result.chain[0].kind == "File"

    def test_method_in_class_in_file(self):
        """Method -> Class -> File chain."""
        method = _make_node(node_id="m1", kind="Method", name="foo", fqn="A\\B::foo()")
        cls = _make_node(node_id="c1", kind="Class", name="B", fqn="A\\B")
        file = _make_node(node_id="f1", kind="File", name="B.php", fqn="src/B.php")

        with patch("src.orchestration.simple.get_owners_chain", return_value=[method, cls, file]):
            result = _build_owners(MagicMock(spec=QueryRunner), "m1")

        assert len(result.chain) == 3
        assert result.chain[0].kind == "Method"
        assert result.chain[1].kind == "Class"
        assert result.chain[2].kind == "File"

    def test_run_owners_resolves_symbol(self):
        """run_owners resolves the symbol first."""
        runner = MagicMock(spec=QueryRunner)
        target = _make_node()

        with patch("src.orchestration.simple.resolve_symbol", return_value=[target]) as mock_resolve, \
             patch("src.orchestration.simple.get_owners_chain", return_value=[target]):
            run_owners(runner, "App\\Service\\OrderService::createOrder()")

        mock_resolve.assert_called_once_with(runner, "App\\Service\\OrderService::createOrder()")

    def test_run_owners_symbol_not_found(self):
        """run_owners raises ValueError when symbol not found."""
        runner = MagicMock(spec=QueryRunner)

        with patch("src.orchestration.simple.resolve_symbol", return_value=[]):
            with pytest.raises(ValueError, match="Symbol not found"):
                run_owners(runner, "Nonexistent\\Symbol")

    def test_run_owners_by_id_not_found(self):
        """run_owners_by_id raises ValueError when node not found."""
        runner = MagicMock(spec=QueryRunner)

        with patch("src.orchestration.simple.fetch_node", return_value=None):
            with pytest.raises(ValueError, match="Node not found"):
                run_owners_by_id(runner, "nonexistent-id")


class TestOwnersToDict:
    """Test to_dict serialization for owners output."""

    def test_chain_to_dict(self):
        """to_dict produces correct structure."""
        method = _make_node(node_id="m1", kind="Method", fqn="A::foo()", start_line=10)
        cls = _make_node(node_id="c1", kind="Class", fqn="A", start_line=5)
        result = OwnersResult(chain=[method, cls])
        d = result.to_dict()

        assert len(d["chain"]) == 2
        assert d["chain"][0]["id"] == "m1"
        assert d["chain"][0]["kind"] == "Method"
        assert d["chain"][0]["line"] == 11  # 0-based to 1-based
        assert d["chain"][1]["id"] == "c1"

    def test_chain_to_dict_none_line(self):
        """to_dict handles None start_line."""
        node = _make_node(node_id="n1", start_line=None)
        result = OwnersResult(chain=[node])
        d = result.to_dict()
        assert d["chain"][0]["line"] is None

    def test_empty_chain(self):
        """Empty chain produces empty list."""
        result = OwnersResult(chain=[])
        d = result.to_dict()
        assert d["chain"] == []


@requires_neo4j
class TestOwnersIntegration:
    """Integration tests for owners against loaded Neo4j database."""

    @pytest.fixture(autouse=True)
    def _ensure_data(self, loaded_database):
        _reload_if_empty(loaded_database)

    def test_method_has_owners(self, loaded_database):
        """A method should have at least a class and file as owners."""
        runner = QueryRunner(loaded_database)
        result = run_owners(
            runner, "App\\Service\\OrderService::createOrder()"
        )
        # Chain should have at least 2 elements: method + at least one parent
        assert len(result.chain) >= 2
        # First element is the target method
        assert result.chain[0].kind == "Method"
        # Last element should be a File
        assert result.chain[-1].kind == "File"

    def test_class_has_file_owner(self, loaded_database):
        """A class should have at least a file as owner."""
        runner = QueryRunner(loaded_database)
        result = run_owners(runner, "App\\Entity\\Order")
        assert len(result.chain) >= 2
        assert result.chain[0].kind == "Class"
        assert result.chain[-1].kind == "File"

    def test_file_has_self_only(self, loaded_database):
        """A file node's chain is just the file itself."""
        runner = QueryRunner(loaded_database)
        # Find a file node first
        file_record = runner.execute_single(
            "MATCH (n:Node {kind: 'File'}) RETURN n LIMIT 1"
        )
        if file_record is None:
            pytest.skip("No File nodes in database")
        from src.db.result_mapper import record_to_node
        file_node = record_to_node(file_record)
        result = run_owners_by_id(runner, file_node.node_id)
        assert len(result.chain) == 1
        assert result.chain[0].kind == "File"

    def test_nonexistent_symbol_raises(self, loaded_database):
        """Nonexistent symbol should raise ValueError."""
        runner = QueryRunner(loaded_database)
        with pytest.raises(ValueError, match="Symbol not found"):
            run_owners(runner, "Totally\\NonExistent\\Symbol\\ZZZZ")

    def test_to_dict_json_output(self, loaded_database):
        """to_dict should produce valid JSON-serializable output."""
        import json

        runner = QueryRunner(loaded_database)
        result = run_owners(runner, "App\\Entity\\Order")
        d = result.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert len(parsed["chain"]) >= 2
