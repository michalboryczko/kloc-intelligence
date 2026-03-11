"""Tests for pure logic helper functions.

Tests use mock data only -- no Neo4j required.
"""

from src.logic.graph_helpers import (
    member_display_name,
    sort_entries_by_priority,
    sort_entries_by_location,
    format_method_fqn,
    is_internal_reference,
)


# =============================================================================
# member_display_name
# =============================================================================


class TestMemberDisplayName:
    """Tests for member_display_name()."""

    def test_method_gets_parens(self):
        assert member_display_name("Method", "save") == "save()"

    def test_function_gets_parens(self):
        assert member_display_name("Function", "array_map") == "array_map()"

    def test_property_gets_dollar_sign(self):
        assert member_display_name("Property", "name") == "$name"

    def test_property_already_has_dollar_sign(self):
        assert member_display_name("Property", "$name") == "$name"

    def test_constant_unchanged(self):
        assert member_display_name("Constant", "MAX_SIZE") == "MAX_SIZE"

    def test_class_unchanged(self):
        assert member_display_name("Class", "OrderService") == "OrderService"

    def test_empty_name(self):
        assert member_display_name("Method", "") == "()"

    def test_interface_unchanged(self):
        assert member_display_name("Interface", "Countable") == "Countable"


# =============================================================================
# sort_entries_by_priority
# =============================================================================


class TestSortEntriesByPriority:
    """Tests for sort_entries_by_priority()."""

    def test_sorts_by_ref_type_priority(self):
        entries = [
            {"ref_type": "type_hint", "file": "a.php", "line": 1},
            {"ref_type": "instantiation", "file": "a.php", "line": 1},
            {"ref_type": "extends", "file": "a.php", "line": 1},
        ]
        result = sort_entries_by_priority(entries)
        assert result[0]["ref_type"] == "instantiation"
        assert result[1]["ref_type"] == "extends"
        assert result[2]["ref_type"] == "type_hint"

    def test_same_priority_sorted_by_file(self):
        entries = [
            {"ref_type": "extends", "file": "b.php", "line": 1},
            {"ref_type": "extends", "file": "a.php", "line": 1},
        ]
        result = sort_entries_by_priority(entries)
        assert result[0]["file"] == "a.php"
        assert result[1]["file"] == "b.php"

    def test_same_priority_same_file_sorted_by_line(self):
        entries = [
            {"ref_type": "method_call", "file": "a.php", "line": 20},
            {"ref_type": "method_call", "file": "a.php", "line": 10},
        ]
        result = sort_entries_by_priority(entries)
        assert result[0]["line"] == 10
        assert result[1]["line"] == 20

    def test_unknown_ref_type_gets_high_priority_number(self):
        entries = [
            {"ref_type": "unknown", "file": "a.php", "line": 1},
            {"ref_type": "instantiation", "file": "a.php", "line": 1},
        ]
        result = sort_entries_by_priority(entries)
        assert result[0]["ref_type"] == "instantiation"
        assert result[1]["ref_type"] == "unknown"

    def test_none_file_handled(self):
        entries = [
            {"ref_type": "method_call", "file": None, "line": None},
            {"ref_type": "method_call", "file": "a.php", "line": 1},
        ]
        result = sort_entries_by_priority(entries)
        assert result[0]["file"] is None  # "" sorts before "a.php"
        assert result[1]["file"] == "a.php"

    def test_empty_list(self):
        assert sort_entries_by_priority([]) == []

    def test_custom_priority(self):
        custom = {"a": 0, "b": 1}
        entries = [
            {"ref_type": "b", "file": "x", "line": 1},
            {"ref_type": "a", "file": "x", "line": 1},
        ]
        result = sort_entries_by_priority(entries, ref_type_priority=custom)
        assert result[0]["ref_type"] == "a"
        assert result[1]["ref_type"] == "b"

    def test_does_not_mutate_input(self):
        entries = [
            {"ref_type": "type_hint", "file": "a", "line": 1},
            {"ref_type": "instantiation", "file": "a", "line": 1},
        ]
        original_order = [e["ref_type"] for e in entries]
        sort_entries_by_priority(entries)
        assert [e["ref_type"] for e in entries] == original_order

    def test_missing_keys_handled(self):
        """Entries with missing keys use empty defaults."""
        entries = [
            {"ref_type": "method_call"},
            {"ref_type": "extends"},
        ]
        result = sort_entries_by_priority(entries)
        assert result[0]["ref_type"] == "extends"
        assert result[1]["ref_type"] == "method_call"


# =============================================================================
# sort_entries_by_location
# =============================================================================


class TestSortEntriesByLocation:
    """Tests for sort_entries_by_location()."""

    def test_sorts_by_file_then_line(self):
        entries = [
            {"file": "b.php", "line": 10},
            {"file": "a.php", "line": 20},
            {"file": "a.php", "line": 5},
        ]
        result = sort_entries_by_location(entries)
        assert result[0] == {"file": "a.php", "line": 5}
        assert result[1] == {"file": "a.php", "line": 20}
        assert result[2] == {"file": "b.php", "line": 10}

    def test_none_file_first(self):
        entries = [
            {"file": "a.php", "line": 1},
            {"file": None, "line": None},
        ]
        result = sort_entries_by_location(entries)
        assert result[0]["file"] is None

    def test_empty_list(self):
        assert sort_entries_by_location([]) == []

    def test_does_not_mutate_input(self):
        entries = [{"file": "b.php", "line": 1}, {"file": "a.php", "line": 1}]
        original = list(entries)
        sort_entries_by_location(entries)
        assert entries == original


# =============================================================================
# format_method_fqn
# =============================================================================


class TestFormatMethodFqn:
    """Tests for format_method_fqn()."""

    def test_method_gets_parens(self):
        assert format_method_fqn("App\\Svc::create", "Method") == "App\\Svc::create()"

    def test_method_already_has_parens(self):
        assert format_method_fqn("App\\Svc::create()", "Method") == "App\\Svc::create()"

    def test_function_not_modified(self):
        assert format_method_fqn("getUser", "Function") == "getUser"

    def test_class_not_modified(self):
        assert format_method_fqn("App\\Svc", "Class") == "App\\Svc"

    def test_property_not_modified(self):
        assert format_method_fqn("App\\Svc::$repo", "Property") == "App\\Svc::$repo"


# =============================================================================
# is_internal_reference
# =============================================================================


class TestIsInternalReference:
    """Tests for is_internal_reference()."""

    def test_target_in_ancestors(self):
        ancestors = ["method:m", "class:Target", "file:f"]
        assert is_internal_reference(ancestors, "class:Target") is True

    def test_target_not_in_ancestors(self):
        ancestors = ["method:m", "class:Other", "file:f"]
        assert is_internal_reference(ancestors, "class:Target") is False

    def test_empty_ancestors(self):
        assert is_internal_reference([], "class:Target") is False

    def test_target_is_first_ancestor(self):
        ancestors = ["class:Target"]
        assert is_internal_reference(ancestors, "class:Target") is True
