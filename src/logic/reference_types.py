"""Reference type inference and constants.

Ported from kloc-cli's reference_types.py. Classifies HOW a source node
references a target based on edge type, target kind, source kind, and
pre-fetched type_hint data from Neo4j.
"""

# =============================================================================
# Depth Chaining Rules (R8)
# =============================================================================
# Only these reference types represent actual call/data flow relationships
# and should be followed when expanding USED BY depth N -> N+1.
# Structural/declarative references (type_hint, extends, implements, use_trait)
# are leaf nodes -- they do not imply that callers of the source are callers
# of the target.
CHAINABLE_REFERENCE_TYPES = {"method_call", "property_access", "instantiation", "static_call", "caller"}


# Reference type priority for sorting USED BY entries
REF_TYPE_PRIORITY = {
    "instantiation": 0,
    "extends": 1,
    "implements": 1,
    "property_type": 2,
    "method_call": 3,
    "static_call": 3,
    "property_access": 4,
    "parameter_type": 5,
    "return_type": 5,
    "type_hint": 6,
}


def infer_reference_type(
    edge_type: str,
    target_kind: str | None,
    source_kind: str | None,
    source_id: str,
    target_id: str,
    source_name: str | None = None,
    has_arg_type_hint: bool = False,
    has_return_type_hint: bool = False,
    has_class_property_type_hint: bool = False,
    has_source_class_property_type_hint: bool = False,
) -> str:
    """Infer reference type from edge type, target kind, and source context.

    This is the central classification function. It determines HOW a source
    node references a target based on edge metadata and pre-fetched type_hint
    data from Neo4j.

    Classification rules:
    1. Direct edge mappings: extends, implements, uses_trait
    2. For "uses" edges with known target kind:
       - Method -> "method_call"
       - Property -> "property_access"
       - Class/Interface/Trait/Enum -> sub-classify via source kind and type_hints
       - Constant -> "constant_access"
       - Function -> "function_call"
       - Argument -> "argument_ref"
       - Variable -> "variable_ref"
    3. Fallback -> "uses"

    Args:
        edge_type: The relationship type (e.g., "uses", "extends").
        target_kind: Kind of the target node (e.g., "Class", "Method").
        source_kind: Kind of the source node (e.g., "Method", "Argument").
        source_id: Node ID of the source.
        target_id: Node ID of the target.
        source_name: Name of the source node (needed for __construct check).
        has_arg_type_hint: True if any Argument child of the source has a
            type_hint edge to the target.
        has_return_type_hint: True if the source Method/Function itself has
            a type_hint edge to the target.
        has_class_property_type_hint: True if the parent class of a __construct
            source has a Property child with type_hint to target (constructor
            promotion).
        has_source_class_property_type_hint: True if the source is a
            Class/Interface/Trait/Enum and one of its Property children has
            a type_hint to target.

    Returns:
        A reference type string (e.g., "method_call", "parameter_type", "extends").
    """
    # 1. Direct edge type mappings
    if edge_type == "extends":
        return "extends"
    if edge_type == "implements":
        return "implements"
    if edge_type == "uses_trait":
        return "uses_trait"

    # 2. For 'uses' edges, infer from target node kind
    if edge_type == "uses" and target_kind:
        if target_kind == "Method":
            return "method_call"
        if target_kind == "Property":
            return "property_access"
        if target_kind in ("Class", "Interface", "Trait", "Enum"):
            return _classify_type_reference(
                source_kind=source_kind,
                source_name=source_name,
                has_arg_type_hint=has_arg_type_hint,
                has_return_type_hint=has_return_type_hint,
                has_class_property_type_hint=has_class_property_type_hint,
                has_source_class_property_type_hint=has_source_class_property_type_hint,
            )
        if target_kind == "Constant":
            return "constant_access"
        if target_kind == "Function":
            return "function_call"
        if target_kind == "Argument":
            return "argument_ref"
        if target_kind == "Variable":
            return "variable_ref"

    # 3. Fallback
    return "uses"


def _classify_type_reference(
    source_kind: str | None,
    source_name: str | None,
    has_arg_type_hint: bool,
    has_return_type_hint: bool,
    has_class_property_type_hint: bool,
    has_source_class_property_type_hint: bool,
) -> str:
    """Sub-classify a 'uses' edge to a Class/Interface/Trait/Enum target.

    Distinguishes between parameter_type, return_type, property_type, and
    type_hint based on the source node kind and pre-fetched type_hint data.

    Args:
        source_kind: Kind of the source node.
        source_name: Name of the source node.
        has_arg_type_hint: Any Argument child has type_hint to target.
        has_return_type_hint: Method/Function itself has type_hint to target.
        has_class_property_type_hint: Parent class Property has type_hint
            (constructor promotion).
        has_source_class_property_type_hint: Source class's Property child has
            type_hint to target.

    Returns:
        One of: "parameter_type", "return_type", "property_type", "type_hint".
    """
    if source_kind is None:
        return "type_hint"

    # Source is an Argument -> parameter_type
    if source_kind == "Argument":
        return "parameter_type"

    # Source is a Property -> property_type
    if source_kind == "Property":
        return "property_type"

    # Source is Method or Function -> check type_hint edges
    if source_kind in ("Method", "Function"):
        if has_arg_type_hint:
            return "parameter_type"
        if has_return_type_hint:
            return "return_type"
        # Constructor promotion: source is __construct, check parent class
        # Property children for type_hint to target
        if source_name == "__construct" and has_class_property_type_hint:
            return "property_type"
        # No type_hint edges matched -> fallback
        return "type_hint"

    # Source is a Class/Interface/Trait/Enum -> check Property children
    if source_kind in ("Class", "Interface", "Trait", "Enum"):
        if has_source_class_property_type_hint:
            return "property_type"
        return "type_hint"

    # Any other source kind (File, etc.) -> type_hint
    return "type_hint"
