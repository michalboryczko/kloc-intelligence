"""Tests for sot.json parser and Neo4j import pipeline."""

from pathlib import Path

import pytest

from src.db.importer import (
    EdgeSpec,
    ImportValidationError,
    NodeSpec,
    edge_to_props,
    extract_signature,
    import_edges,
    import_nodes,
    load_sot,
    node_to_props,
    parse_sot,
    validate_import,
)

from .conftest import requires_neo4j

SOT_PATH = Path("/Users/michal/dev/ai/kloc/artifacts/kloc-dev/context-final/sot.json")


# --- S01: Parser tests ---


class TestParseSot:
    """Test sot.json parsing with the real dataset."""

    def test_parse_sot_counts(self):
        """Test that parse_sot reads the correct number of nodes and edges."""
        nodes, edges = parse_sot(SOT_PATH)
        assert len(nodes) == 1154, f"Expected 1154 nodes, got {len(nodes)}"
        assert len(edges) == 2697, f"Expected 2697 edges, got {len(edges)}"

    def test_load_sot_version(self):
        """Test that the sot.json version is parsed correctly."""
        data = load_sot(SOT_PATH)
        assert data.version == "2.0"

    def test_load_sot_metadata(self):
        """Test that metadata is parsed."""
        data = load_sot(SOT_PATH)
        assert "generated_at" in data.metadata

    def test_node_kinds_present(self):
        """Test that expected node kinds exist in the dataset."""
        data = load_sot(SOT_PATH)
        kinds = {n.kind for n in data.nodes}
        # Dataset has 10 of 13 kinds (no Const, Enum, EnumCase, Function, Trait)
        expected = {"Class", "Interface", "Method", "Property", "Argument", "Value", "Call", "File"}
        assert expected.issubset(kinds), f"Missing kinds: {expected - kinds}"

    def test_edge_types_present(self):
        """Test that expected edge types exist in the dataset."""
        data = load_sot(SOT_PATH)
        types = {e.type for e in data.edges}
        # Dataset has 12 of 13 types (no return_type)
        expected = {
            "contains",
            "uses",
            "extends",
            "implements",
            "overrides",
            "type_hint",
            "calls",
            "receiver",
            "argument",
            "produces",
            "assigned_from",
            "type_of",
        }
        assert expected.issubset(types), f"Missing types: {expected - types}"


class TestNodeToProps:
    """Test node_to_props mapping."""

    def test_basic_fields(self):
        """Test that basic fields are mapped correctly."""
        node = NodeSpec(
            id="node:abc123",
            kind="Class",
            name="User",
            fqn="App\\Entity\\User",
            symbol="scip-php composer test 1.0 App/Entity/User#",
        )
        props = node_to_props(node)
        assert props["node_id"] == "node:abc123"
        assert props["kind"] == "Class"
        assert props["name"] == "User"
        assert props["fqn"] == "App\\Entity\\User"
        assert props["symbol"] == "scip-php composer test 1.0 App/Entity/User#"

    def test_file_field(self):
        """Test that file is included when present."""
        node = NodeSpec(
            id="node:abc",
            kind="Class",
            name="User",
            fqn="App\\Entity\\User",
            symbol="s",
            file="src/Entity/User.php",
        )
        props = node_to_props(node)
        assert props["file"] == "src/Entity/User.php"

    def test_file_absent(self):
        """Test that file is not included when absent."""
        node = NodeSpec(
            id="node:abc",
            kind="Class",
            name="User",
            fqn="App\\Entity\\User",
            symbol="s",
        )
        props = node_to_props(node)
        assert "file" not in props

    def test_range_flattening(self):
        """Test that range dict is flattened into individual properties."""
        node = NodeSpec(
            id="node:abc",
            kind="Method",
            name="getId",
            fqn="App\\Entity\\User::getId()",
            symbol="s",
            range={"start_line": 10, "start_col": 4, "end_line": 10, "end_col": 15},
        )
        props = node_to_props(node)
        assert props["start_line"] == 10
        assert props["start_col"] == 4
        assert props["end_line"] == 10
        assert props["end_col"] == 15

    def test_enclosing_range_flattening(self):
        """Test that enclosing_range is flattened with enclosing_ prefix."""
        node = NodeSpec(
            id="node:abc",
            kind="Method",
            name="getId",
            fqn="App\\Entity\\User::getId()",
            symbol="s",
            enclosing_range={"start_line": 8, "start_col": 0, "end_line": 12, "end_col": 5},
        )
        props = node_to_props(node)
        assert props["enclosing_start_line"] == 8
        assert props["enclosing_start_col"] == 0
        assert props["enclosing_end_line"] == 12
        assert props["enclosing_end_col"] == 5

    def test_documentation_preserved(self):
        """Test that documentation list is preserved."""
        node = NodeSpec(
            id="node:abc",
            kind="Method",
            name="getId",
            fqn="App\\Entity\\User::getId()",
            symbol="s",
            documentation=["```php\npublic function getId(): int\n```"],
        )
        props = node_to_props(node)
        assert props["documentation"] == ["```php\npublic function getId(): int\n```"]

    def test_value_kind(self):
        """Test that value_kind is included when present."""
        node = NodeSpec(
            id="node:abc",
            kind="Value",
            name="$user",
            fqn="some::fqn",
            symbol="s",
            value_kind="variable",
        )
        props = node_to_props(node)
        assert props["value_kind"] == "variable"

    def test_call_kind(self):
        """Test that call_kind is included when present."""
        node = NodeSpec(
            id="node:abc",
            kind="Call",
            name="getUser",
            fqn="some::fqn",
            symbol="s",
            call_kind="method_call",
        )
        props = node_to_props(node)
        assert props["call_kind"] == "method_call"

    def test_type_symbol(self):
        """Test that type_symbol is included when present."""
        node = NodeSpec(
            id="node:abc",
            kind="Value",
            name="$user",
            fqn="some::fqn",
            symbol="s",
            type_symbol="scip-php composer test 1.0 App/Entity/User#",
        )
        props = node_to_props(node)
        assert props["type_symbol"] == "scip-php composer test 1.0 App/Entity/User#"


class TestSignatureExtraction:
    """Test extract_signature ported from kloc-cli."""

    def test_simple_method_signature(self):
        """Test extracting a simple method signature."""
        node = NodeSpec(
            id="node:abc",
            kind="Method",
            name="getId",
            fqn="App\\Entity\\User::getId()",
            symbol="s",
            documentation=["```php\npublic function getId(): int\n```"],
        )
        sig = extract_signature(node)
        assert sig == "getId(): int"

    def test_method_with_params(self):
        """Test extracting signature with parameters."""
        node = NodeSpec(
            id="node:abc",
            kind="Method",
            name="process",
            fqn="App\\Component\\OrderProcessorInterface::process()",
            symbol="s",
            documentation=[
                "```php\npublic function process(\\App\\Entity\\Order $order): \\App\\Entity\\Order\n```"
            ],
        )
        sig = extract_signature(node)
        assert sig is not None
        assert "process(" in sig
        assert "$order" in sig

    def test_class_no_signature(self):
        """Test that non-method/function nodes return None."""
        node = NodeSpec(
            id="node:abc",
            kind="Class",
            name="User",
            fqn="App\\Entity\\User",
            symbol="s",
            documentation=["Some class docs"],
        )
        sig = extract_signature(node)
        assert sig is None

    def test_no_documentation(self):
        """Test that nodes without documentation return None."""
        node = NodeSpec(
            id="node:abc",
            kind="Method",
            name="getId",
            fqn="App\\Entity\\User::getId()",
            symbol="s",
        )
        sig = extract_signature(node)
        assert sig is None

    def test_signature_stored_in_props(self):
        """Test that computed signature ends up in node_to_props output."""
        node = NodeSpec(
            id="node:abc",
            kind="Method",
            name="getId",
            fqn="App\\Entity\\User::getId()",
            symbol="s",
            documentation=["```php\npublic function getId(): int\n```"],
        )
        props = node_to_props(node)
        assert "signature" in props
        assert props["signature"] == "getId(): int"

    def test_no_signature_not_in_props(self):
        """Test that props don't have signature key when none extracted."""
        node = NodeSpec(
            id="node:abc",
            kind="Class",
            name="User",
            fqn="App\\Entity\\User",
            symbol="s",
        )
        props = node_to_props(node)
        assert "signature" not in props

    def test_signatures_in_real_dataset(self):
        """Test that real dataset produces signatures for Method nodes."""
        data = load_sot(SOT_PATH)
        method_nodes = [n for n in data.nodes if n.kind == "Method"]
        assert len(method_nodes) > 0
        sigs = [extract_signature(n) for n in method_nodes]
        non_none = [s for s in sigs if s is not None]
        # Most methods with documentation should yield signatures
        assert len(non_none) > 0, "Expected at least some signatures from Method nodes"


class TestEdgeToProps:
    """Test edge_to_props mapping."""

    def test_basic_fields(self):
        """Test that basic edge fields are mapped."""
        edge = EdgeSpec(type="contains", source="node:aaa", target="node:bbb")
        props = edge_to_props(edge)
        assert props["type"] == "contains"
        assert props["source_id"] == "node:aaa"
        assert props["target_id"] == "node:bbb"

    def test_location_fields(self):
        """Test that location dict is mapped to loc_file and loc_line."""
        edge = EdgeSpec(
            type="uses",
            source="node:aaa",
            target="node:bbb",
            location={"file": "src/Entity/User.php", "line": 42},
        )
        props = edge_to_props(edge)
        assert props["loc_file"] == "src/Entity/User.php"
        assert props["loc_line"] == 42

    def test_position_field(self):
        """Test that position is included when present."""
        edge = EdgeSpec(
            type="argument",
            source="node:aaa",
            target="node:bbb",
            position=0,
        )
        props = edge_to_props(edge)
        assert props["position"] == 0

    def test_expression_and_parameter(self):
        """Test that expression and parameter are included."""
        edge = EdgeSpec(
            type="calls",
            source="node:aaa",
            target="node:bbb",
            expression="$this->getUser()",
            parameter="userId",
        )
        props = edge_to_props(edge)
        assert props["expression"] == "$this->getUser()"
        assert props["parameter"] == "userId"

    def test_optional_fields_absent(self):
        """Test that optional fields are absent when not set."""
        edge = EdgeSpec(type="extends", source="node:aaa", target="node:bbb")
        props = edge_to_props(edge)
        assert "loc_file" not in props
        assert "loc_line" not in props
        assert "position" not in props
        assert "expression" not in props
        assert "parameter" not in props


# --- S02-S04: Integration tests ---


@requires_neo4j
class TestImportIntegration:
    """Integration tests requiring a running Neo4j instance."""

    def test_full_import_and_validate(self, neo4j_connection):
        """Test full import pipeline: parse, import nodes, import edges, validate."""
        from src.db.schema import drop_all, ensure_schema

        # Clean state
        drop_all(neo4j_connection)
        ensure_schema(neo4j_connection)

        # Parse
        nodes, edges = parse_sot(SOT_PATH)
        assert len(nodes) == 1154
        assert len(edges) == 2697

        # Import
        node_count = import_nodes(neo4j_connection, nodes)
        assert node_count == 1154

        edge_count = import_edges(neo4j_connection, edges)
        assert edge_count == 2697

        # Validate
        report = validate_import(neo4j_connection, 1154, 2697)
        assert report["valid"] is True
        assert report["node_count"] == 1154
        assert report["edge_count"] == 2697
        assert report["node_match"] is True
        assert report["edge_match"] is True

        # Check kind distribution
        assert "Method" in report["kind_counts"]
        assert "Class" in report["kind_counts"]

        # Check relationship types
        assert "CONTAINS" in report["type_counts"]
        assert "USES" in report["type_counts"]

    def test_validation_failure(self, neo4j_connection):
        """Test that validation raises on mismatch."""
        from src.db.schema import drop_all, ensure_schema

        drop_all(neo4j_connection)
        ensure_schema(neo4j_connection)

        with pytest.raises(ImportValidationError):
            validate_import(neo4j_connection, 100, 200)

    def test_node_labels_correct(self, neo4j_connection):
        """Test that nodes get correct kind-specific labels."""
        from src.db.schema import drop_all, ensure_schema

        drop_all(neo4j_connection)
        ensure_schema(neo4j_connection)

        nodes, edges = parse_sot(SOT_PATH)
        import_nodes(neo4j_connection, nodes)

        with neo4j_connection.session() as session:
            # Check that Method nodes have both :Node and :Method labels
            result = session.run("MATCH (n:Method) RETURN count(n) AS cnt").single()["cnt"]
            method_count = sum(1 for n in nodes if n["kind"] == "Method")
            assert result == method_count

            # Check Class label
            result = session.run("MATCH (n:Class) RETURN count(n) AS cnt").single()["cnt"]
            class_count = sum(1 for n in nodes if n["kind"] == "Class")
            assert result == class_count

    def test_signature_stored_in_neo4j(self, neo4j_connection):
        """Test that computed signatures are stored as node properties."""
        from src.db.schema import drop_all, ensure_schema

        drop_all(neo4j_connection)
        ensure_schema(neo4j_connection)

        nodes, _ = parse_sot(SOT_PATH)
        import_nodes(neo4j_connection, nodes)

        with neo4j_connection.session() as session:
            result = session.run(
                "MATCH (n:Method) WHERE n.signature IS NOT NULL RETURN count(n) AS cnt"
            ).single()["cnt"]
            # At least some methods should have signatures
            assert result > 0

    def test_import_with_clear(self, neo4j_connection):
        """Test that importing twice with clear gives same result."""
        from src.db.schema import drop_all, ensure_schema

        # First import
        drop_all(neo4j_connection)
        ensure_schema(neo4j_connection)
        nodes, edges = parse_sot(SOT_PATH)
        import_nodes(neo4j_connection, nodes)
        import_edges(neo4j_connection, edges)

        # Second import (with clear)
        drop_all(neo4j_connection)
        ensure_schema(neo4j_connection)
        import_nodes(neo4j_connection, nodes)
        import_edges(neo4j_connection, edges)

        report = validate_import(neo4j_connection, 1154, 2697)
        assert report["valid"] is True


# --- S05: CLI command test ---


@requires_neo4j
class TestImportCLI:
    """Test the CLI import command end-to-end."""

    def test_import_command(self, neo4j_connection):
        """Test that the CLI import command runs successfully."""
        from typer.testing import CliRunner

        from src.cli import app
        from src.db.schema import drop_all

        # Ensure clean state
        drop_all(neo4j_connection)

        runner = CliRunner()
        result = runner.invoke(app, ["import", str(SOT_PATH)])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert "Parsed 1,154 nodes" in result.output
        assert "2,697 edges" in result.output
        assert "Import complete" in result.output

    def test_import_command_no_validate(self, neo4j_connection):
        """Test import command with --no-validate flag."""
        from typer.testing import CliRunner

        from src.cli import app
        from src.db.schema import drop_all

        drop_all(neo4j_connection)

        runner = CliRunner()
        result = runner.invoke(app, ["import", str(SOT_PATH), "--no-validate"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert "Import complete" in result.output
