"""Tests for reference type inference.

Tests all 20+ branches of infer_reference_type() with mock data only --
no Neo4j required.
"""

from src.logic.reference_types import (
    CHAINABLE_REFERENCE_TYPES,
    REF_TYPE_PRIORITY,
    infer_reference_type,
)


# =============================================================================
# Constants Tests
# =============================================================================


class TestConstants:
    """Tests for exported constants."""

    def test_chainable_reference_types_is_a_set(self):
        assert isinstance(CHAINABLE_REFERENCE_TYPES, set)

    def test_chainable_contains_expected_types(self):
        expected = {"method_call", "property_access", "instantiation", "static_call", "caller"}
        assert CHAINABLE_REFERENCE_TYPES == expected

    def test_type_hint_not_chainable(self):
        assert "type_hint" not in CHAINABLE_REFERENCE_TYPES

    def test_extends_not_chainable(self):
        assert "extends" not in CHAINABLE_REFERENCE_TYPES

    def test_implements_not_chainable(self):
        assert "implements" not in CHAINABLE_REFERENCE_TYPES

    def test_ref_type_priority_is_dict(self):
        assert isinstance(REF_TYPE_PRIORITY, dict)

    def test_instantiation_highest_priority(self):
        assert REF_TYPE_PRIORITY["instantiation"] == 0

    def test_extends_implements_same_priority(self):
        assert REF_TYPE_PRIORITY["extends"] == REF_TYPE_PRIORITY["implements"]

    def test_type_hint_lowest_named_priority(self):
        assert REF_TYPE_PRIORITY["type_hint"] == 6

    def test_method_call_static_call_same_priority(self):
        assert REF_TYPE_PRIORITY["method_call"] == REF_TYPE_PRIORITY["static_call"]


# =============================================================================
# Direct Edge Type Mappings
# =============================================================================


class TestDirectEdgeMappings:
    """Tests for edges that map directly to reference types."""

    def test_extends_edge(self):
        result = infer_reference_type(
            edge_type="extends",
            target_kind="Class",
            source_kind="Class",
            source_id="src",
            target_id="tgt",
        )
        assert result == "extends"

    def test_extends_edge_ignores_target_kind(self):
        """extends edge is used regardless of target kind."""
        result = infer_reference_type(
            edge_type="extends",
            target_kind="Method",  # Unusual but possible
            source_kind="Class",
            source_id="src",
            target_id="tgt",
        )
        assert result == "extends"

    def test_extends_edge_ignores_source_kind(self):
        result = infer_reference_type(
            edge_type="extends",
            target_kind="Class",
            source_kind=None,
            source_id="src",
            target_id="tgt",
        )
        assert result == "extends"

    def test_implements_edge(self):
        result = infer_reference_type(
            edge_type="implements",
            target_kind="Interface",
            source_kind="Class",
            source_id="src",
            target_id="tgt",
        )
        assert result == "implements"

    def test_uses_trait_edge(self):
        result = infer_reference_type(
            edge_type="uses_trait",
            target_kind="Trait",
            source_kind="Class",
            source_id="src",
            target_id="tgt",
        )
        assert result == "uses_trait"


# =============================================================================
# Uses Edge with Target Kind
# =============================================================================


class TestUsesEdgeTargetKind:
    """Tests for 'uses' edges classified by target kind."""

    def test_uses_method_returns_method_call(self):
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Method",
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "method_call"

    def test_uses_property_returns_property_access(self):
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Property",
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "property_access"

    def test_uses_constant_returns_constant_access(self):
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Constant",
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "constant_access"

    def test_uses_function_returns_function_call(self):
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Function",
            source_kind="File",
            source_id="src",
            target_id="tgt",
        )
        assert result == "function_call"

    def test_uses_argument_returns_argument_ref(self):
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Argument",
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "argument_ref"

    def test_uses_variable_returns_variable_ref(self):
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Variable",
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "variable_ref"


# =============================================================================
# Uses Edge to Class/Interface/Trait/Enum -- Sub-classification
# =============================================================================


class TestUsesEdgeTypeClassification:
    """Tests for 'uses' edges to Class/Interface/Trait/Enum that need sub-classification."""

    def test_argument_source_returns_parameter_type(self):
        """Source is Argument -> parameter_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Argument",
            source_id="arg:1",
            target_id="class:1",
        )
        assert result == "parameter_type"

    def test_property_source_returns_property_type(self):
        """Source is Property -> property_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Interface",
            source_kind="Property",
            source_id="prop:1",
            target_id="iface:1",
        )
        assert result == "property_type"

    def test_method_source_with_arg_type_hint_returns_parameter_type(self):
        """Method source + Argument child has type_hint to target -> parameter_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Method",
            source_id="method:1",
            target_id="class:1",
            has_arg_type_hint=True,
        )
        assert result == "parameter_type"

    def test_method_source_with_return_type_hint_returns_return_type(self):
        """Method source + method has type_hint to target -> return_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Method",
            source_id="method:1",
            target_id="class:1",
            has_return_type_hint=True,
        )
        assert result == "return_type"

    def test_method_source_arg_type_hint_takes_precedence_over_return(self):
        """When both arg and return type_hint exist, parameter_type wins."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Method",
            source_id="method:1",
            target_id="class:1",
            has_arg_type_hint=True,
            has_return_type_hint=True,
        )
        assert result == "parameter_type"

    def test_function_source_with_return_type_hint(self):
        """Function source + function has type_hint to target -> return_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Function",
            source_id="func:1",
            target_id="class:1",
            has_return_type_hint=True,
        )
        assert result == "return_type"

    def test_function_source_with_arg_type_hint(self):
        """Function source + Argument child has type_hint -> parameter_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Function",
            source_id="func:1",
            target_id="class:1",
            has_arg_type_hint=True,
        )
        assert result == "parameter_type"

    def test_constructor_promotion_returns_property_type(self):
        """__construct source + parent class Property has type_hint -> property_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Method",
            source_id="method:construct",
            target_id="class:1",
            source_name="__construct",
            has_class_property_type_hint=True,
        )
        assert result == "property_type"

    def test_constructor_promotion_requires_construct_name(self):
        """Non-__construct method with class property type_hint -> type_hint (not property_type)."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Method",
            source_id="method:other",
            target_id="class:1",
            source_name="someMethod",
            has_class_property_type_hint=True,
        )
        assert result == "type_hint"

    def test_method_source_no_type_hints_returns_type_hint(self):
        """Method source with no type_hint edges -> type_hint fallback."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Method",
            source_id="method:1",
            target_id="class:1",
        )
        assert result == "type_hint"

    def test_class_source_with_property_type_hint(self):
        """Class source + Property child has type_hint -> property_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Interface",
            source_kind="Class",
            source_id="class:svc",
            target_id="iface:1",
            has_source_class_property_type_hint=True,
        )
        assert result == "property_type"

    def test_class_source_no_property_type_hint(self):
        """Class source with no Property type_hint -> type_hint."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Interface",
            source_kind="Class",
            source_id="class:svc",
            target_id="iface:1",
        )
        assert result == "type_hint"

    def test_interface_source_with_property_type_hint(self):
        """Interface source + Property child type_hint -> property_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Interface",
            source_id="iface:1",
            target_id="class:1",
            has_source_class_property_type_hint=True,
        )
        assert result == "property_type"

    def test_trait_source_with_property_type_hint(self):
        """Trait source + Property child type_hint -> property_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Trait",
            source_id="trait:1",
            target_id="class:1",
            has_source_class_property_type_hint=True,
        )
        assert result == "property_type"

    def test_enum_source_with_property_type_hint(self):
        """Enum source + Property child type_hint -> property_type."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Enum",
            source_id="enum:1",
            target_id="class:1",
            has_source_class_property_type_hint=True,
        )
        assert result == "property_type"

    def test_file_source_returns_type_hint(self):
        """File source (import statement) -> type_hint."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="File",
            source_id="file:1",
            target_id="class:1",
        )
        assert result == "type_hint"

    def test_no_source_kind_returns_type_hint(self):
        """source_kind=None -> type_hint fallback."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind=None,
            source_id="unknown",
            target_id="class:1",
        )
        assert result == "type_hint"

    def test_uses_enum_target(self):
        """uses edge to Enum with Method source falls through to type_hint."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Enum",
            source_kind="Method",
            source_id="method:1",
            target_id="enum:1",
        )
        assert result == "type_hint"

    def test_uses_trait_target_via_uses_edge(self):
        """uses edge to Trait (not uses_trait edge) -> type_hint."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Trait",
            source_kind="Method",
            source_id="method:1",
            target_id="trait:1",
        )
        assert result == "type_hint"


# =============================================================================
# Fallback / Edge Cases
# =============================================================================


class TestFallbackCases:
    """Tests for fallback and edge cases."""

    def test_uses_without_target_kind_returns_uses(self):
        """uses edge with target_kind=None -> 'uses' fallback."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind=None,
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "uses"

    def test_unknown_edge_type_returns_uses(self):
        """Unknown edge type -> 'uses' fallback."""
        result = infer_reference_type(
            edge_type="some_unknown_type",
            target_kind="Class",
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "uses"

    def test_empty_edge_type_returns_uses(self):
        """Empty string edge type -> 'uses' fallback."""
        result = infer_reference_type(
            edge_type="",
            target_kind="Class",
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "uses"

    def test_uses_unknown_target_kind_returns_uses(self):
        """uses edge with unknown target kind -> 'uses' fallback."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="SomeUnknownKind",
            source_kind="Method",
            source_id="src",
            target_id="tgt",
        )
        assert result == "uses"

    def test_all_type_hint_flags_false_for_method_source(self):
        """Method source with all flags False -> type_hint."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Method",
            source_id="m",
            target_id="c",
            source_name="foo",
            has_arg_type_hint=False,
            has_return_type_hint=False,
            has_class_property_type_hint=False,
            has_source_class_property_type_hint=False,
        )
        assert result == "type_hint"

    def test_constructor_no_class_property_type_hint(self):
        """__construct without class property type_hint -> type_hint."""
        result = infer_reference_type(
            edge_type="uses",
            target_kind="Class",
            source_kind="Method",
            source_id="m",
            target_id="c",
            source_name="__construct",
            has_class_property_type_hint=False,
        )
        assert result == "type_hint"
