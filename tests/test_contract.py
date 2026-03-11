"""Contract tests for kloc-intelligence context output.

Validates that context query output matches the JSON schema defined in
kloc-contracts/kloc-cli-context.json. Ports the contract testing approach
from kloc-reference-project-php/contract-tests-kloc-cli/.

Tests cover:
- Output format matches the JSON schema
- Required fields present (target, usedBy, uses, maxDepth)
- camelCase field names in output
- 1-based line numbers
- Null fields omitted where appropriate
- Context bidirectional structure (USED BY + USES)
- Constructor redirect (__construct -> Class USED BY)
"""

import json
from pathlib import Path

import pytest

from src.models.node import NodeData
from src.models.results import (
    ArgumentInfo,
    ContextEntry,
    ContextResult,
    DefinitionInfo,
    MemberRef,
)
from src.models.output import ContextOutput

# =========================================================================
# Helpers
# =========================================================================

SCHEMA_PATH = (
    Path(__file__).parent.parent.parent / "kloc-contracts" / "kloc-cli-context.json"
)


def _load_schema() -> dict:
    """Load the contract schema for context output."""
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def _make_node(**overrides) -> NodeData:
    """Create a test NodeData with defaults."""
    defaults = {
        "node_id": "test-node-1",
        "kind": "Method",
        "name": "findById",
        "fqn": "App\\Repo::findById()",
        "symbol": "scip-php . . App/Repo#findById().",
        "file": "src/Repo.php",
        "start_line": 20,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _make_context_entry(**overrides) -> ContextEntry:
    """Create a test ContextEntry with defaults."""
    defaults = {
        "depth": 1,
        "node_id": "entry-1",
        "fqn": "App\\Service::process()",
        "kind": "Method",
        "file": "src/Service.php",
        "line": 42,
    }
    defaults.update(overrides)
    return ContextEntry(**defaults)


def _collect_fqns(entries: list[dict]) -> list[str]:
    """Recursively collect all FQNs from a context tree."""
    fqns = []
    for entry in entries:
        fqns.append(entry.get("fqn", ""))
        if entry.get("children"):
            fqns.extend(_collect_fqns(entry["children"]))
        if entry.get("implementations"):
            fqns.extend(_collect_fqns(entry["implementations"]))
    return fqns


def _collect_all_entries(entries: list[dict]) -> list[dict]:
    """Recursively collect all entries from a context tree."""
    result = []
    for entry in entries:
        result.append(entry)
        if entry.get("children"):
            result.extend(_collect_all_entries(entry["children"]))
        if entry.get("implementations"):
            result.extend(_collect_all_entries(entry["implementations"]))
    return result


# =========================================================================
# Schema validation contract tests
# =========================================================================


class TestContractRequiredFields:
    """Validate required top-level fields are present."""

    def test_target_field_present(self):
        """Output must contain a 'target' object."""
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "target" in d

    def test_max_depth_field_present(self):
        """Output must contain 'maxDepth' integer."""
        target = _make_node()
        result = ContextResult(target=target, max_depth=2)
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "maxDepth" in d
        assert isinstance(d["maxDepth"], int)
        assert d["maxDepth"] >= 1

    def test_used_by_field_present(self):
        """Output must contain 'usedBy' array."""
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "usedBy" in d
        assert isinstance(d["usedBy"], list)

    def test_uses_field_present(self):
        """Output must contain 'uses' array."""
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "uses" in d
        assert isinstance(d["uses"], list)

    def test_definition_present_when_provided(self):
        """'definition' should be present when available."""
        target = _make_node()
        definition = DefinitionInfo(
            fqn="App\\Repo::findById()",
            kind="Method",
            file="src/Repo.php",
            line=20,
        )
        result = ContextResult(target=target, max_depth=1, definition=definition)
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "definition" in d

    def test_definition_absent_when_none(self):
        """'definition' should be absent when not available."""
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        output = ContextOutput.from_result(result)
        d = output.to_dict()
        assert "definition" not in d


class TestContractTargetFields:
    """Validate target object has correct structure."""

    def test_target_has_fqn(self):
        target = _make_node(fqn="App\\Entity\\Order")
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        assert d["target"]["fqn"] == "App\\Entity\\Order"

    def test_target_has_file(self):
        target = _make_node(file="src/Entity/Order.php")
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        assert d["target"]["file"] == "src/Entity/Order.php"

    def test_target_has_1_based_line(self):
        target = _make_node(start_line=8)  # 0-based
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        assert d["target"]["line"] == 9  # 1-based

    def test_target_signature_for_methods(self):
        target = _make_node(
            kind="Method",
            signature="findById(int $id): ?Order",
        )
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        assert d["target"]["signature"] == "findById(int $id): ?Order"

    def test_target_no_signature_for_class(self):
        target = _make_node(kind="Class", fqn="App\\Entity\\Order")
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        assert "signature" not in d["target"]


class TestContractCamelCaseFields:
    """Validate camelCase field naming throughout output."""

    def test_top_level_camel_case(self):
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        # Must be camelCase
        assert "maxDepth" in d
        assert "usedBy" in d
        # Must NOT be snake_case
        assert "max_depth" not in d
        assert "used_by" not in d

    def test_entry_ref_type_camel_case(self):
        entry = _make_context_entry(ref_type="instantiation")
        target = _make_node(kind="Class", fqn="App\\Order")
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        ub = d["usedBy"][0]
        assert "refType" in ub
        assert "ref_type" not in ub

    def test_entry_on_kind_camel_case(self):
        entry = _make_context_entry(on_kind="property")
        target = _make_node()
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        ub = d["usedBy"][0]
        assert "onKind" in ub
        assert "on_kind" not in ub

    def test_definition_return_type_camel_case(self):
        target = _make_node()
        definition = DefinitionInfo(
            fqn="App\\Repo::findById()",
            kind="Method",
            return_type={"fqn": "App\\Order", "name": "Order"},
        )
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        assert "returnType" in d["definition"]
        assert "return_type" not in d["definition"]

    def test_definition_constructor_deps_camel_case(self):
        target = _make_node(kind="Class", fqn="App\\Order")
        definition = DefinitionInfo(
            fqn="App\\Order",
            kind="Class",
            constructor_deps=[{"name": "$repo", "type": "Repo"}],
        )
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        assert "constructorDeps" in d["definition"]
        assert "constructor_deps" not in d["definition"]

    def test_entry_access_count_camel_case(self):
        entry = _make_context_entry(access_count=5, method_count=3)
        target = _make_node(kind="Class", fqn="App\\Order")
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        ub = d["usedBy"][0]
        assert "accessCount" in ub
        assert "methodCount" in ub
        assert "access_count" not in ub
        assert "method_count" not in ub


class TestContractLineNumbers:
    """Validate all line numbers are 1-based."""

    def test_target_line_is_1_based(self):
        target = _make_node(start_line=0)
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        assert d["target"]["line"] == 1  # 0+1

    def test_entry_line_is_1_based(self):
        entry = _make_context_entry(line=0)
        target = _make_node()
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        assert d["usedBy"][0]["line"] == 1  # 0+1

    def test_definition_line_is_1_based(self):
        target = _make_node()
        definition = DefinitionInfo(fqn="x", kind="Method", line=0)
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        assert d["definition"]["line"] == 1

    def test_member_ref_line_is_1_based(self):
        ref = MemberRef(
            target_name="save",
            target_fqn="App\\Repo::save()",
            line=0,
            on_line=0,
        )
        entry = _make_context_entry(member_ref=ref)
        target = _make_node()
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        mr = d["usedBy"][0]["member_ref"]
        assert mr["line"] == 1
        assert mr["on_line"] == 1

    def test_declared_in_line_is_1_based(self):
        target = _make_node()
        definition = DefinitionInfo(
            fqn="App\\Foo::bar()",
            kind="Method",
            declared_in={"fqn": "App\\Foo", "file": "f.php", "line": 0},
        )
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        assert d["definition"]["declaredIn"]["line"] == 1


class TestContractNullFieldOmission:
    """Validate that None/null values are omitted from output where expected."""

    def test_no_none_in_optional_entry_fields(self):
        """Optional fields should be omitted, not serialized as None."""
        entry = _make_context_entry()  # no optional fields set
        target = _make_node()
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        ub = d["usedBy"][0]
        # These optional fields should not be present
        for key in [
            "signature", "refType", "callee", "on", "onKind",
            "member_ref", "arguments", "args", "result_var",
            "entry_type", "variable_name", "variable_symbol",
            "variable_type", "source_call", "crossed_from",
            "sites", "via", "property", "accessCount", "methodCount",
            "implementations", "via_interface",
        ]:
            if key in ub:
                assert ub[key] is not None, f"Field '{key}' is None, should be omitted"

    def test_no_none_in_optional_definition_fields(self):
        """Optional definition fields should be omitted, not None."""
        definition = DefinitionInfo(fqn="App\\Foo", kind="Class")
        target = _make_node(kind="Class", fqn="App\\Foo")
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        defn = d["definition"]
        for key in [
            "signature", "arguments", "returnType", "properties",
            "methods", "extends", "implements", "uses_traits",
            "constructorDeps", "type", "visibility", "promoted",
            "readonly", "static", "value_kind", "source", "declaredIn",
        ]:
            if key in defn:
                assert defn[key] is not None, f"Definition field '{key}' is None"


class TestContractEntryStructure:
    """Validate ContextEntry has required fields per schema."""

    def test_entry_has_required_fields(self):
        entry = _make_context_entry()
        target = _make_node()
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        ub = d["usedBy"][0]
        assert "depth" in ub
        assert "fqn" in ub
        assert "kind" in ub
        assert "children" in ub
        assert isinstance(ub["children"], list)

    def test_entry_depth_is_integer(self):
        entry = _make_context_entry(depth=2)
        target = _make_node()
        result = ContextResult(target=target, max_depth=2, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        assert isinstance(d["usedBy"][0]["depth"], int)
        assert d["usedBy"][0]["depth"] >= 0


class TestContractBidirectionalStructure:
    """Validate context output has both USED BY and USES sections."""

    def test_both_sections_present(self):
        target = _make_node()
        ub_entry = _make_context_entry(fqn="App\\Caller::call()")
        uses_entry = _make_context_entry(fqn="App\\Dep::dep()")
        result = ContextResult(
            target=target,
            max_depth=1,
            used_by=[ub_entry],
            uses=[uses_entry],
        )
        d = ContextOutput.from_result(result).to_dict()
        assert len(d["usedBy"]) == 1
        assert len(d["uses"]) == 1
        assert d["usedBy"][0]["fqn"] == "App\\Caller::call()"
        assert d["uses"][0]["fqn"] == "App\\Dep::dep()"

    def test_empty_sections_are_empty_lists(self):
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        assert d["usedBy"] == []
        assert d["uses"] == []


class TestContractDefinitionStructure:
    """Validate definition metadata structure."""

    def test_definition_required_fields(self):
        target = _make_node()
        definition = DefinitionInfo(
            fqn="App\\Repo::findById()",
            kind="Method",
        )
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        defn = d["definition"]
        assert "fqn" in defn
        assert "kind" in defn

    def test_definition_class_fields(self):
        target = _make_node(kind="Class", fqn="App\\Order")
        definition = DefinitionInfo(
            fqn="App\\Order",
            kind="Class",
            extends="App\\Base",
            implements=["App\\Interface1"],
            uses_traits=["App\\Trait1"],
            properties=[{"name": "$id", "type": "int"}],
            methods=[{"name": "getId", "signature": "getId(): int"}],
            constructor_deps=[{"name": "$repo", "type": "Repo"}],
        )
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        defn = d["definition"]
        assert defn["extends"] == "App\\Base"
        assert defn["implements"] == ["App\\Interface1"]
        assert defn["uses_traits"] == ["App\\Trait1"]
        assert len(defn["properties"]) == 1
        assert len(defn["methods"]) == 1
        assert len(defn["constructorDeps"]) == 1

    def test_definition_property_has_type(self):
        target = _make_node(kind="Property", fqn="App\\Order::$id")
        definition = DefinitionInfo(
            fqn="App\\Order::$id",
            kind="Property",
            return_type={"name": "int", "visibility": "private", "readonly": True},
        )
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        defn = d["definition"]
        assert defn["type"] == "int"
        assert defn["visibility"] == "private"
        assert defn["readonly"] is True

    def test_definition_value_fields(self):
        target = _make_node(kind="Value", fqn="val.0", name="$order")
        definition = DefinitionInfo(
            fqn="val.0",
            kind="Value",
            value_kind="local",
            type_info={"fqn": "App\\Order", "name": "Order"},
            source={"call_fqn": "call.0", "method_fqn": "App\\Repo::find()"},
        )
        result = ContextResult(target=target, max_depth=1, definition=definition)
        d = ContextOutput.from_result(result).to_dict()
        defn = d["definition"]
        assert defn["value_kind"] == "local"
        assert defn["type"] == {"fqn": "App\\Order", "name": "Order"}
        assert "source" in defn


class TestContractSchemaValidation:
    """Validate output against the JSON schema using jsonschema library."""

    @pytest.fixture
    def schema(self):
        """Load the contract JSON schema."""
        return _load_schema()

    def _validate(self, data: dict, schema: dict):
        """Validate data against schema, raising on failure."""
        import jsonschema
        jsonschema.validate(instance=data, schema=schema)

    def test_minimal_output_validates(self, schema):
        """Minimal valid output (empty usedBy/uses, no definition) validates."""
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        self._validate(d, schema)

    def test_method_context_output_validates(self, schema):
        """Method context with entries and definition validates."""
        target = _make_node(
            kind="Method",
            fqn="App\\Service::process()",
            start_line=20,
            signature="process(): void",
        )
        ub_entry = _make_context_entry(
            depth=1,
            fqn="App\\Controller::handle()",
            kind="Method",
            file="src/Controller.php",
            line=10,
            signature="handle(): Response",
        )
        uses_entry = _make_context_entry(
            depth=1,
            fqn="App\\Entity\\Order",
            kind="Class",
            file="src/Entity/Order.php",
            line=5,
            ref_type="instantiation",
        )
        definition = DefinitionInfo(
            fqn="App\\Service::process()",
            kind="Method",
            file="src/Service.php",
            line=20,
            signature="process(): void",
            arguments=[{"name": "$input", "type": "OrderInput"}],
            return_type={"fqn": "void", "name": "void"},
            declared_in={"fqn": "App\\Service", "file": "src/Service.php", "line": 5},
        )
        result = ContextResult(
            target=target,
            max_depth=2,
            used_by=[ub_entry],
            uses=[uses_entry],
            definition=definition,
        )
        d = ContextOutput.from_result(result).to_dict()
        self._validate(d, schema)

    def test_class_context_output_validates(self, schema):
        """Class context with definition validates."""
        target = _make_node(kind="Class", fqn="App\\Entity\\Order")
        definition = DefinitionInfo(
            fqn="App\\Entity\\Order",
            kind="Class",
            file="src/Entity/Order.php",
            line=8,
            properties=[{"name": "$id", "type": "int"}],
            methods=[{"name": "__construct", "signature": "__construct(int $id)"}],
            constructor_deps=[{"name": "$id", "type": "int"}],
        )
        ub_entry = _make_context_entry(
            depth=1,
            fqn="App\\Service::createOrder()",
            kind="Method",
            file="src/Service.php",
            line=30,
            ref_type="instantiation",
        )
        result = ContextResult(
            target=target,
            max_depth=1,
            used_by=[ub_entry],
            definition=definition,
        )
        d = ContextOutput.from_result(result).to_dict()
        self._validate(d, schema)

    def test_entry_with_member_ref_validates(self, schema):
        """Entry with member_ref validates."""
        ref = MemberRef(
            target_name="save()",
            target_fqn="App\\Repo::save()",
            target_kind="Method",
            file="src/Repo.php",
            line=10,
            reference_type="method_call",
            access_chain="$this->repo",
            on_kind="property",
        )
        entry = _make_context_entry(member_ref=ref, signature="handle(): void")
        target = _make_node()
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        self._validate(d, schema)

    def test_entry_with_arguments_validates(self, schema):
        """Entry with rich arguments validates."""
        args = [
            ArgumentInfo(
                position=0,
                param_name="$id",
                value_expr="42",
                value_source="literal",
                value_type="int",
                param_fqn="App\\Foo::bar().$id",
            ),
        ]
        entry = _make_context_entry(arguments=args, signature="bar(int $id): void")
        target = _make_node()
        result = ContextResult(target=target, max_depth=1, uses=[entry])
        d = ContextOutput.from_result(result).to_dict()
        self._validate(d, schema)

    def test_entry_with_sites_validates(self, schema):
        """Entry with multi-site validates, and line is omitted."""
        entry = _make_context_entry(
            sites=[{"method": "App\\Foo::bar()", "line": 10}],
            ref_type="type_hint",
        )
        target = _make_node(kind="Class", fqn="App\\Order")
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        self._validate(d, schema)
        ub = d["usedBy"][0]
        assert "sites" in ub
        assert "line" not in ub

    def test_entry_with_via_interface_validates(self, schema):
        """Entry with via_interface flag validates."""
        entry = _make_context_entry(via_interface=True)
        target = _make_node()
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        self._validate(d, schema)
