"""Output format validation tests for kloc-intelligence context output.

Validates that kloc-intelligence context --json output conforms to the
contract schema at kloc-contracts/kloc-cli-context.json.

Tests cover:
- Schema validation of snapshot outputs against the JSON schema
- camelCase field names throughout the output
- No None values in output (should be omitted)
- Line numbers are 1-based (no 0 or negative values)
- Recursive structure validation
"""

import json
from pathlib import Path

import jsonschema
import pytest

from src.models.node import NodeData
from src.models.results import (
    ContextEntry,
    ContextResult,
)
from src.models.output import ContextOutput


# =========================================================================
# Schema and snapshot loading
# =========================================================================

SCHEMA_PATH = (
    Path(__file__).parent.parent.parent / "kloc-contracts" / "kloc-cli-context.json"
)
SNAPSHOT_PATH = (
    Path(__file__).parent.parent.parent / "tests" / "snapshot-1802262244.json"
)
CASES_PATH = Path(__file__).parent.parent.parent / "tests" / "cases.json"


@pytest.fixture(scope="module")
def schema():
    """Load the contract JSON schema."""
    with open(SCHEMA_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def snapshot_data():
    """Load all golden snapshot outputs."""
    with open(SNAPSHOT_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def cases():
    """Load test case definitions."""
    with open(CASES_PATH) as f:
        return json.load(f)["cases"]


# =========================================================================
# Schema validation against snapshots
# =========================================================================


def _snapshot_case_ids():
    """Generate case IDs from snapshot file for parametrize."""
    with open(SNAPSHOT_PATH) as f:
        data = json.load(f)
    return list(data.keys())


@pytest.mark.parametrize("case_name", _snapshot_case_ids())
class TestSnapshotSchemaValidation:
    """Validate each snapshot output against the contract schema."""

    def test_validates_against_schema(self, case_name, schema, snapshot_data):
        """Each snapshot output must validate against the JSON schema."""
        output = snapshot_data[case_name]
        try:
            jsonschema.validate(instance=output, schema=schema)
        except jsonschema.ValidationError as e:
            pytest.fail(
                f"Schema validation failed for '{case_name}': {e.message}\n"
                f"Path: {list(e.absolute_path)}"
            )


# =========================================================================
# camelCase field name validation
# =========================================================================


# Fields that must use camelCase (the contract schema defines these)
EXPECTED_CAMEL_CASE = {
    "maxDepth", "usedBy", "refType", "onKind", "returnType",
    "constructorDeps", "accessCount", "methodCount", "via_interface",
    "member_ref", "source_call", "entry_type", "variable_name",
    "variable_symbol", "variable_type", "crossed_from", "result_var",
    "value_kind", "uses_traits", "declaredIn",
}

# Fields that must NOT appear (snake_case equivalents of camelCase)
FORBIDDEN_SNAKE_CASE = {
    "max_depth",  # -> maxDepth
    "used_by",  # -> usedBy
    "ref_type",  # -> refType
    "on_kind",  # -> onKind
    "return_type",  # -> returnType
    "constructor_deps",  # -> constructorDeps
    "access_count",  # -> accessCount
    "method_count",  # -> methodCount
    "declared_in",  # -> declaredIn
    "property_name",  # -> property
}


def _collect_all_keys(obj, path="$") -> list[tuple[str, str]]:
    """Recursively collect all keys and their paths from a JSON structure."""
    keys = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.append((k, f"{path}.{k}"))
            keys.extend(_collect_all_keys(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            keys.extend(_collect_all_keys(item, f"{path}[{i}]"))
    return keys


@pytest.mark.parametrize("case_name", _snapshot_case_ids()[:10])
class TestSnapshotCamelCase:
    """Validate camelCase field naming across snapshot outputs.

    Only tests first 10 cases for performance (all share the same serializer).
    """

    def test_no_forbidden_snake_case_keys(self, case_name, snapshot_data):
        """No forbidden snake_case keys should appear in output."""
        output = snapshot_data[case_name]
        all_keys = _collect_all_keys(output)
        violations = [
            (key, path) for key, path in all_keys
            if key in FORBIDDEN_SNAKE_CASE
        ]
        assert not violations, (
            f"Found forbidden snake_case keys in '{case_name}':\n"
            + "\n".join(f"  {key} at {path}" for key, path in violations)
        )


# =========================================================================
# None value checking
# =========================================================================


def _find_none_values(obj, path="$") -> list[str]:
    """Recursively find any None/null values in the output.

    We allow None for 'file', 'line', 'kind' fields (per schema: type: ["string", "null"]).
    We report all others.
    """
    nullable_fields = {"file", "line", "kind", "target_kind", "param_name",
                       "value_expr", "value_source"}
    nones = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v is None and k not in nullable_fields:
                nones.append(f"{path}.{k}")
            elif v is not None:
                nones.extend(_find_none_values(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            nones.extend(_find_none_values(item, f"{path}[{i}]"))
    return nones


@pytest.mark.parametrize("case_name", _snapshot_case_ids()[:10])
class TestSnapshotNoNoneValues:
    """Validate that optional fields are omitted rather than set to None."""

    def test_no_unexpected_none_values(self, case_name, snapshot_data):
        """Non-nullable fields should be omitted, not serialized as None."""
        output = snapshot_data[case_name]
        nones = _find_none_values(output)
        assert not nones, (
            f"Found unexpected None values in '{case_name}':\n"
            + "\n".join(f"  {path}" for path in nones)
        )


# =========================================================================
# Line number validation (1-based, positive)
# =========================================================================


def _collect_line_values(obj, path="$") -> list[tuple[int, str]]:
    """Recursively collect all 'line' field values and their paths."""
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "line" and isinstance(v, int):
                lines.append((v, path))
            elif k == "on_line" and isinstance(v, int):
                lines.append((v, f"{path}.on_line"))
            else:
                lines.extend(_collect_line_values(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            lines.extend(_collect_line_values(item, f"{path}[{i}]"))
    return lines


@pytest.mark.parametrize("case_name", _snapshot_case_ids())
class TestSnapshotLineNumbers:
    """Validate that all line numbers are 1-based (positive integers)."""

    def test_all_lines_are_1_based(self, case_name, snapshot_data):
        """No line field should have value 0 or negative."""
        output = snapshot_data[case_name]
        lines = _collect_line_values(output)
        invalid = [(value, path) for value, path in lines if value < 1]
        assert not invalid, (
            f"Found non-1-based line numbers in '{case_name}':\n"
            + "\n".join(f"  {value} at {path}" for value, path in invalid)
        )


# =========================================================================
# Recursive structure validation
# =========================================================================


def _validate_entry_structure(entry: dict, path: str) -> list[str]:
    """Validate a single context entry has correct structure."""
    errors = []
    required = {"depth", "fqn", "kind", "children"}
    for field in required:
        if field not in entry:
            errors.append(f"{path}: missing required field '{field}'")

    if "children" in entry and not isinstance(entry["children"], list):
        errors.append(f"{path}.children: must be a list")

    # Recurse into children
    for i, child in enumerate(entry.get("children", [])):
        errors.extend(_validate_entry_structure(child, f"{path}.children[{i}]"))

    # Recurse into implementations
    for i, impl in enumerate(entry.get("implementations", [])):
        errors.extend(_validate_entry_structure(impl, f"{path}.implementations[{i}]"))

    return errors


@pytest.mark.parametrize("case_name", _snapshot_case_ids()[:10])
class TestSnapshotEntryStructure:
    """Validate recursive entry structure in snapshot outputs."""

    def test_used_by_entries_have_required_fields(self, case_name, snapshot_data):
        output = snapshot_data[case_name]
        errors = []
        for i, entry in enumerate(output.get("usedBy", [])):
            errors.extend(_validate_entry_structure(entry, f"usedBy[{i}]"))
        assert not errors, (
            f"Entry structure errors in '{case_name}':\n"
            + "\n".join(f"  {e}" for e in errors)
        )

    def test_uses_entries_have_required_fields(self, case_name, snapshot_data):
        output = snapshot_data[case_name]
        errors = []
        for i, entry in enumerate(output.get("uses", [])):
            errors.extend(_validate_entry_structure(entry, f"uses[{i}]"))
        assert not errors, (
            f"Entry structure errors in '{case_name}':\n"
            + "\n".join(f"  {e}" for e in errors)
        )


# =========================================================================
# NodeKind coverage validation
# =========================================================================


class TestNodeKindCoverage:
    """Validate all major NodeKinds are represented in the snapshot corpus."""

    def test_all_categories_present_in_cases(self, cases):
        """Test cases cover all 6 categories: class, interface, method, property,
        value-param, value-local."""
        categories = {c["category"] for c in cases}
        expected = {"class", "interface", "method", "property", "value-param", "value-local"}
        assert expected.issubset(categories), (
            f"Missing categories: {expected - categories}"
        )

    def test_all_node_kinds_in_snapshot_targets(self, snapshot_data):
        """Snapshot targets should cover all major node kinds."""
        # Extract the kinds of the definition (which reflects the target kind)
        target_kinds = set()
        for name, output in snapshot_data.items():
            defn = output.get("definition", {})
            kind = defn.get("kind")
            if kind:
                target_kinds.add(kind)
        # Must cover at least: Class, Interface, Method, Property, Value
        expected = {"Class", "Interface", "Method", "Property", "Value"}
        assert expected.issubset(target_kinds), (
            f"Missing target kinds: {expected - target_kinds}. Found: {target_kinds}"
        )

    def test_entry_kinds_coverage(self, snapshot_data):
        """Context entries should reference multiple kinds."""
        entry_kinds = set()
        for name, output in snapshot_data.items():
            for section in ["usedBy", "uses"]:
                for entry in output.get(section, []):
                    kind = entry.get("kind")
                    if kind:
                        entry_kinds.add(kind)
        # Must include at least Method and Class
        assert "Method" in entry_kinds
        assert "Class" in entry_kinds


# =========================================================================
# Unit tests for output model conversion
# =========================================================================


def _make_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "n-1",
        "kind": "Method",
        "name": "findById",
        "fqn": "App\\Repo::findById()",
        "symbol": "sym",
        "file": "src/Repo.php",
        "start_line": 20,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _make_entry(**overrides) -> ContextEntry:
    defaults = {
        "depth": 1,
        "node_id": "e-1",
        "fqn": "App\\Service::process()",
        "kind": "Method",
        "file": "src/Service.php",
        "line": 42,
    }
    defaults.update(overrides)
    return ContextEntry(**defaults)


class TestOutputModelConversion:
    """Test the ContextOutput model serialization for format compliance."""

    def test_round_trip_preserves_structure(self):
        """Converting ContextResult -> ContextOutput -> dict preserves structure."""
        target = _make_node()
        entry = _make_entry()
        result = ContextResult(target=target, max_depth=2, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        assert d["target"]["fqn"] == target.fqn
        assert d["maxDepth"] == 2
        assert len(d["usedBy"]) == 1
        assert d["usedBy"][0]["fqn"] == "App\\Service::process()"

    def test_sites_replaces_line(self):
        """When sites are present, the line field should be removed."""
        entry = _make_entry(
            sites=[{"method": "App\\Foo::bar()", "line": 10}],
            ref_type="type_hint",
        )
        target = _make_node(kind="Class", fqn="App\\Foo")
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        ub = d["usedBy"][0]
        assert "sites" in ub
        assert "line" not in ub

    def test_property_field_renaming(self):
        """property_name should serialize as 'property'."""
        entry = _make_entry(property_name="$email")
        target = _make_node(kind="Class", fqn="App\\Foo")
        result = ContextResult(target=target, max_depth=1, used_by=[entry])
        d = ContextOutput.from_result(result).to_dict()
        ub = d["usedBy"][0]
        assert "property" in ub
        assert "property_name" not in ub

    def test_empty_output_validates_against_schema(self):
        """Empty context output should validate against schema."""
        target = _make_node()
        result = ContextResult(target=target, max_depth=1)
        d = ContextOutput.from_result(result).to_dict()
        with open(SCHEMA_PATH) as f:
            schema = json.load(f)
        jsonschema.validate(instance=d, schema=schema)
