"""Tests for result_mapper and NodeData model."""

from src.db.query_runner import QueryRunner
from src.db.result_mapper import record_to_node, records_to_nodes
from src.models.node import NodeData

from .conftest import requires_neo4j


class TestNodeData:
    """Unit tests for NodeData model."""

    def test_id_property(self):
        """Test that id property returns node_id."""
        node = NodeData(
            node_id="node:abc",
            kind="Class",
            name="Order",
            fqn="App\\Entity\\Order",
            symbol="scip-php composer test 1.0 App/Entity/Order#",
        )
        assert node.id == "node:abc"

    def test_location_str_with_file_and_line(self):
        """Test location_str with file and line (0-indexed to 1-indexed)."""
        node = NodeData(
            node_id="node:abc",
            kind="Class",
            name="Order",
            fqn="App\\Entity\\Order",
            symbol="s",
            file="src/Entity/Order.php",
            start_line=9,
        )
        assert node.location_str == "src/Entity/Order.php:10"

    def test_location_str_with_file_only(self):
        """Test location_str with file but no line."""
        node = NodeData(
            node_id="node:abc",
            kind="Class",
            name="Order",
            fqn="App\\Entity\\Order",
            symbol="s",
            file="src/Entity/Order.php",
        )
        assert node.location_str == "src/Entity/Order.php"

    def test_location_str_unknown(self):
        """Test location_str when no file info."""
        node = NodeData(
            node_id="node:abc",
            kind="Class",
            name="Order",
            fqn="App\\Entity\\Order",
            symbol="s",
        )
        assert node.location_str == "<unknown>"

    def test_display_name_class(self):
        """Test display_name for a class returns FQN."""
        node = NodeData(
            node_id="node:abc",
            kind="Class",
            name="Order",
            fqn="App\\Entity\\Order",
            symbol="s",
        )
        assert node.display_name == "App\\Entity\\Order"

    def test_display_name_method_with_signature(self):
        """Test display_name for method with signature."""
        node = NodeData(
            node_id="node:abc",
            kind="Method",
            name="getId",
            fqn="App\\Entity\\Order::getId()",
            symbol="s",
            signature="getId(): int",
        )
        assert node.display_name == "App\\Entity\\Order::getId(): int"

    def test_display_name_method_without_signature(self):
        """Test display_name for method without signature falls back to FQN."""
        node = NodeData(
            node_id="node:abc",
            kind="Method",
            name="getId",
            fqn="App\\Entity\\Order::getId()",
            symbol="s",
        )
        assert node.display_name == "App\\Entity\\Order::getId()"

    def test_display_name_function_with_signature(self):
        """Test display_name for function with signature (no ::)."""
        node = NodeData(
            node_id="node:abc",
            kind="Function",
            name="helper",
            fqn="helper()",
            symbol="s",
            signature="helper(): void",
        )
        assert node.display_name == "helper(): void"

    def test_default_documentation(self):
        """Test that documentation defaults to empty list."""
        node = NodeData(
            node_id="node:abc",
            kind="Class",
            name="Order",
            fqn="App\\Entity\\Order",
            symbol="s",
        )
        assert node.documentation == []

    def test_optional_fields_default_none(self):
        """Test that optional fields default to None."""
        node = NodeData(
            node_id="node:abc",
            kind="Class",
            name="Order",
            fqn="App\\Entity\\Order",
            symbol="s",
        )
        assert node.file is None
        assert node.start_line is None
        assert node.start_col is None
        assert node.end_line is None
        assert node.end_col is None
        assert node.value_kind is None
        assert node.type_symbol is None
        assert node.call_kind is None
        assert node.signature is None
        assert node.enclosing_start_line is None
        assert node.enclosing_start_col is None
        assert node.enclosing_end_line is None
        assert node.enclosing_end_col is None


@requires_neo4j
class TestRecordToNode:
    """Integration tests for record_to_node mapping from real Neo4j records."""

    def test_record_to_node_class(self, loaded_database):
        """Test mapping a Class node record."""
        runner = QueryRunner(loaded_database)
        record = runner.execute_single(
            "MATCH (n:Node {fqn: $fqn}) RETURN n",
            fqn="App\\Entity\\Order",
        )
        assert record is not None
        node = record_to_node(record)
        assert isinstance(node, NodeData)
        assert node.kind == "Class"
        assert node.name == "Order"
        assert node.fqn == "App\\Entity\\Order"
        assert node.node_id is not None
        assert node.symbol is not None
        assert node.file is not None

    def test_record_to_node_method_with_signature(self, loaded_database):
        """Test mapping a Method node with signature."""
        runner = QueryRunner(loaded_database)
        records = runner.execute(
            "MATCH (n:Node {kind: 'Method'}) WHERE n.signature IS NOT NULL RETURN n LIMIT 1"
        )
        assert len(records) == 1
        node = record_to_node(records[0])
        assert node.kind == "Method"
        assert node.signature is not None
        assert len(node.signature) > 0

    def test_record_to_node_value(self, loaded_database):
        """Test mapping a Value node."""
        runner = QueryRunner(loaded_database)
        records = runner.execute("MATCH (n:Node {kind: 'Value'}) RETURN n LIMIT 1")
        assert len(records) == 1
        node = record_to_node(records[0])
        assert node.kind == "Value"
        assert node.value_kind is not None

    def test_records_to_nodes(self, loaded_database):
        """Test mapping multiple records."""
        runner = QueryRunner(loaded_database)
        records = runner.execute("MATCH (n:Node {kind: 'Class'}) RETURN n LIMIT 5")
        nodes = records_to_nodes(records)
        assert len(nodes) == 5
        assert all(isinstance(n, NodeData) for n in nodes)
        assert all(n.kind == "Class" for n in nodes)

    def test_records_to_nodes_empty(self, loaded_database):
        """Test mapping empty record list."""
        nodes = records_to_nodes([])
        assert nodes == []

    def test_documentation_list(self, loaded_database):
        """Test that documentation is returned as a list."""
        runner = QueryRunner(loaded_database)
        records = runner.execute(
            "MATCH (n:Node {kind: 'Method'}) WHERE n.documentation IS NOT NULL RETURN n LIMIT 1"
        )
        assert len(records) == 1
        node = record_to_node(records[0])
        assert isinstance(node.documentation, list)
        assert len(node.documentation) > 0

    def test_enclosing_range_mapped(self, loaded_database):
        """Test that enclosing range properties are mapped."""
        runner = QueryRunner(loaded_database)
        records = runner.execute(
            "MATCH (n:Node) WHERE n.enclosing_start_line IS NOT NULL RETURN n LIMIT 1"
        )
        if records:
            node = record_to_node(records[0])
            assert node.enclosing_start_line is not None
            assert node.enclosing_end_line is not None
