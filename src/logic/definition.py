"""Definition builders for the context command.

Ported from kloc-cli's queries/definition.py. All functions take pre-fetched
data dicts from Cypher queries instead of SoTIndex.

The main entry point is build_definition(data) which dispatches to
kind-specific builders based on node kind.
"""

import re  # used in parse_property_doc
from typing import Optional

from ..models.results import DefinitionInfo



def build_definition(data: dict) -> DefinitionInfo:
    """Build definition metadata for a symbol from pre-fetched query data.

    Args:
        data: Dict with keys:
            - node: dict with fqn, kind, file, start_line, signature, documentation, value_kind
            - parent: optional dict with fqn, kind, file, start_line (containing scope)
            - children: list of child dicts with node_id, kind, name, signature, documentation, fqn
            - child_type_hints: dict mapping child_id -> list of {fqn, name} type hint targets
            - type_hints: list of {fqn, name} for the node's own type_hint edges
            - inheritance: dict with extends_fqn, implements_fqns, uses_trait_fqns
            - overrides: dict mapping child_id -> parent_id (or None)
            - assigned_from: list of source node dicts
            - value_source: dict with af (assigned_from node), call, callee, types

    Returns:
        DefinitionInfo with symbol metadata.
    """
    node = data.get("node")
    if not node:
        return DefinitionInfo(fqn="unknown", kind="unknown")

    info = DefinitionInfo(
        fqn=node.get("fqn", "unknown"),
        kind=node.get("kind", "unknown"),
        file=node.get("file"),
        line=node.get("start_line"),
        signature=node.get("signature"),
    )

    # Resolve containing class/method
    parent = data.get("parent")
    if parent:
        info.declared_in = {
            "fqn": parent.get("fqn"),
            "kind": parent.get("kind"),
            "file": parent.get("file"),
            "line": parent.get("start_line"),
        }

    kind = info.kind
    if kind in ("Method", "Function"):
        build_method_definition(data, info)
    elif kind in ("Class", "Interface", "Trait", "Enum"):
        build_class_definition(data, info)
    elif kind == "Property":
        build_property_definition(data, info)
    elif kind == "Argument":
        build_argument_definition(data, info)
    elif kind == "Value":
        build_value_definition(data, info)

    return info


def build_method_definition(data: dict, info: DefinitionInfo) -> None:
    """Populate definition for Method/Function nodes.

    Collects typed arguments from children and return type from type_hint edges.
    Arguments are sorted by their position in the method signature.
    """
    children = data.get("children", [])
    child_type_hints = data.get("child_type_hints", {})

    # Collect typed arguments
    for child in children:
        if child.get("kind") == "Argument":
            arg_dict: dict = {"name": child.get("name"), "position": None}
            # Resolve type from type_hint edges
            child_id = child.get("node_id")
            type_hints = child_type_hints.get(child_id, [])
            if type_hints:
                arg_dict["type"] = type_hints[0].get("name")
            info.arguments.append(arg_dict)

    # Children are already in sot.json insertion order (via ordinal on CONTAINS edge)

    # Resolve return type from type_hint edges on the method itself
    type_hints = data.get("type_hints", [])
    if type_hints:
        info.return_type = {"fqn": type_hints[0].get("fqn"), "name": type_hints[0].get("name")}


def build_class_definition(data: dict, info: DefinitionInfo) -> None:
    """Populate definition for Class/Interface/Trait/Enum nodes.

    For Classes: adds properties with metadata (type, visibility, promoted,
    readonly, static), methods with tags ([override], [abstract], [inherited]),
    constructor_deps for promoted constructor parameters, extends, implements.

    For Interfaces: delegates to build_interface_definition.
    """
    if info.kind == "Interface":
        build_interface_definition(data, info)
        return

    children = data.get("children", [])
    child_type_hints = data.get("child_type_hints", {})
    overrides = data.get("overrides", {})
    promoted_properties = data.get("promoted_properties", set())

    for child in children:
        child_kind = child.get("kind")
        child_id = child.get("node_id")

        if child_kind == "Property":
            prop_dict: dict = {"name": child.get("name")}
            # Type from type_hint edges
            type_hints = child_type_hints.get(child_id, [])
            if type_hints:
                prop_dict["type"] = type_hints[0].get("name")

            # Parse property metadata from documentation
            vis, readonly, static, doc_type = parse_property_doc(
                child.get("documentation", []), child.get("name", "")
            )
            if vis:
                prop_dict["visibility"] = vis
            if readonly:
                prop_dict["readonly"] = True
            if static:
                prop_dict["static"] = True

            # If no class type from edges, use type from docs
            if "type" not in prop_dict and doc_type:
                prop_dict["type"] = doc_type

            # Detect promoted
            if child_id in promoted_properties:
                prop_dict["promoted"] = True

            info.properties.append(prop_dict)

        elif child_kind == "Method":
            # Skip __construct -- implied by promoted properties
            if child.get("name") == "__construct":
                continue

            method_dict: dict = {"name": child.get("name")}
            if child.get("signature"):
                method_dict["signature"] = child["signature"]

            # Method tags: [override], [abstract], [inherited]
            tags = []
            # Check if method overrides a parent method
            if child_id and overrides.get(child_id):
                tags.append("override")
            # Check if method is abstract (from PHP signature in documentation)
            documentation = child.get("documentation", [])
            if documentation:
                for doc in documentation:
                    clean = doc.replace("```php", "").replace("```", "").strip()
                    for line in clean.split("\n"):
                        line = line.strip()
                        if "function " in line and "abstract " in line:
                            tags.append("abstract")
                            break
                    if "abstract" in tags:
                        break

            if tags:
                method_dict["tags"] = tags
            info.methods.append(method_dict)

    # Sort methods: override first, then inherited, then regular
    def _method_sort_key(m: dict) -> int:
        tags = m.get("tags", [])
        if "override" in tags:
            return 0
        if "inherited" in tags:
            return 1
        return 2
    info.methods.sort(key=_method_sort_key)

    # Constructor deps: promoted parameters with their types
    for child in children:
        child_kind = child.get("kind")
        child_id = child.get("node_id")
        if child_kind != "Property":
            continue
        # Only promoted properties
        if child_id not in promoted_properties:
            continue
        dep: dict = {"name": child.get("name")}
        # Get type from type_hint edges on the property
        type_hints = child_type_hints.get(child_id, [])
        if type_hints:
            dep["type"] = type_hints[0].get("name")
        else:
            # Try scalar type from docs
            _, _, _, doc_type = parse_property_doc(
                child.get("documentation", []), child.get("name", "")
            )
            if doc_type:
                dep["type"] = doc_type
        info.constructor_deps.append(dep)

    # Children are already in sot.json insertion order (via ordinal on CONTAINS edge).
    # Properties and constructor_deps inherit that order.

    # Inheritance
    inheritance = data.get("inheritance", {})
    if inheritance.get("extends_fqn"):
        info.extends = inheritance["extends_fqn"]
    for impl_fqn in inheritance.get("implements_fqns", []):
        if impl_fqn:
            info.implements.append(impl_fqn)
    for trait_fqn in inheritance.get("uses_trait_fqns", []):
        if trait_fqn:
            info.uses_traits.append(trait_fqn)


def build_interface_definition(data: dict, info: DefinitionInfo) -> None:
    """Populate definition for Interface nodes.

    Shows method signatures only (no properties, no implements).
    Shows extends if interface extends another interface.
    """
    children = data.get("children", [])

    for child in children:
        if child.get("kind") == "Method":
            method_dict: dict = {"name": child.get("name")}
            if child.get("signature"):
                method_dict["signature"] = child["signature"]
            info.methods.append(method_dict)

    # Interface extends (interface extending interface)
    inheritance = data.get("inheritance", {})
    if inheritance.get("extends_fqn"):
        info.extends = inheritance["extends_fqn"]


def build_property_definition(data: dict, info: DefinitionInfo) -> None:
    """Populate definition for Property nodes.

    Extracts: type (from type_hint edges or documentation), visibility,
    promoted (detected via assigned_from -> Value(parameter) in __construct),
    readonly, static -- all parsed from SCIP documentation strings.
    """
    node = data.get("node", {})
    type_hints = data.get("type_hints", [])
    promoted_properties = data.get("promoted_properties", set())
    node_id = node.get("node_id")

    # Type from type_hint edges (class types)
    if type_hints:
        info.return_type = {"fqn": type_hints[0].get("fqn"), "name": type_hints[0].get("name")}

    # Parse visibility, readonly, static, and scalar type from documentation
    documentation = node.get("documentation", []) or []
    name = node.get("name", "")
    vis, readonly, static, doc_type = parse_property_doc(documentation, name)
    if vis:
        if not info.return_type:
            info.return_type = {}
        info.return_type["visibility"] = vis
    if readonly:
        if not info.return_type:
            info.return_type = {}
        info.return_type["readonly"] = True
    if static:
        if not info.return_type:
            info.return_type = {}
        info.return_type["static"] = True

    # If property itself isn't readonly, check if the containing class is readonly
    if not readonly:
        parent = data.get("parent", {})
        if parent and parent.get("kind") == "Class":
            parent_docs = parent.get("documentation", []) or []
            for doc in parent_docs:
                if "readonly class" in doc or "readonly " in doc:
                    readonly = True
                    break
        if readonly:
            if not info.return_type:
                info.return_type = {}
            info.return_type["readonly"] = True

    # If no class type from edges, use type from documentation
    if not info.return_type or "name" not in info.return_type:
        if doc_type:
            if info.return_type is None:
                info.return_type = {}
            info.return_type["name"] = doc_type
            info.return_type["fqn"] = doc_type

    # Detect promoted
    if node_id and node_id in promoted_properties:
        if not info.return_type:
            info.return_type = {}
        info.return_type["promoted"] = True


def parse_property_doc(
    documentation: list[str] | None, name: str
) -> tuple[Optional[str], bool, bool, Optional[str]]:
    """Parse property documentation for visibility, readonly, static, type.

    SCIP documentation for properties looks like:
        ```php\\npublic string $customerEmail\\n```
        ```php\\nprivate static array $sentEmails = []\\n```
        ```php\\nprivate readonly \\App\\Service\\CustomerService $customerService\\n```

    Args:
        documentation: List of SCIP documentation strings.
        name: Property name (e.g., "$customerEmail").

    Returns:
        (visibility, readonly, static, scalar_type)
    """
    visibility = None
    readonly = False
    static = False
    doc_type = None

    if not documentation:
        return visibility, readonly, static, doc_type

    for doc in documentation:
        clean = doc.replace("```php", "").replace("```", "").strip()
        if not clean:
            continue
        # Only look at lines that contain the property name
        for line in clean.split("\n"):
            line = line.strip()
            if name not in line:
                continue
            # Extract visibility
            if line.startswith("public "):
                visibility = "public"
            elif line.startswith("protected "):
                visibility = "protected"
            elif line.startswith("private "):
                visibility = "private"
            # Check modifiers
            if " readonly " in line or line.startswith("readonly "):
                readonly = True
            if " static " in line or line.startswith("static "):
                static = True
            # Extract type: everything between modifiers and the property name
            # Pattern: [visibility] [static] [readonly] TYPE $name
            match = re.search(
                r'(?:public|protected|private)?\s*(?:static\s+)?(?:readonly\s+)?(\S+)\s+\$',
                line
            )
            if match:
                raw_type = match.group(1)
                # Skip if the "type" is just a modifier word
                if raw_type not in ("public", "protected", "private", "static", "readonly"):
                    # Clean up namespace prefix
                    if raw_type.startswith("\\"):
                        raw_type = raw_type.lstrip("\\")
                    # Use short name (last part)
                    doc_type = raw_type.rsplit("\\", 1)[-1] if "\\" in raw_type else raw_type
            break  # Only need first matching doc
        if visibility or doc_type:
            break

    return visibility, readonly, static, doc_type


def build_argument_definition(data: dict, info: DefinitionInfo) -> None:
    """Populate definition for Argument nodes."""
    type_hints = data.get("type_hints", [])
    if type_hints:
        info.return_type = {"fqn": type_hints[0].get("fqn"), "name": type_hints[0].get("name")}


def build_value_definition(data: dict, info: DefinitionInfo) -> None:
    """Populate definition for Value nodes with data flow metadata.

    Adds value_kind (local/parameter/result/literal/constant), type
    resolution via type_of edges, and source resolution via
    assigned_from -> produces -> Call target chain.
    """
    node = data.get("node", {})

    # value_kind: local, parameter, result, literal, constant
    info.value_kind = node.get("value_kind")

    # Type resolution via type_of (supports union types)
    type_of_nodes = data.get("type_of", [])
    if type_of_nodes:
        type_names = [t.get("name") for t in type_of_nodes if t.get("name")]
        if type_names:
            first = type_of_nodes[0]
            if len(type_of_nodes) == 1:
                info.type_info = {"fqn": first.get("fqn"), "name": first.get("name")}
            else:
                info.type_info = {
                    "fqn": "|".join(t.get("fqn", "") for t in type_of_nodes),
                    "name": "|".join(type_names),
                }

    # Source resolution from pre-fetched value_source data
    value_source = data.get("value_source", {})
    if value_source:
        af = value_source.get("af")
        call = value_source.get("call")
        callee = value_source.get("callee")

        if af and af.get("kind") == "Property":
            # Promoted constructor param
            info.source = {
                "call_fqn": None,
                "method_fqn": af.get("fqn"),
                "method_name": f"promotes to {af.get('fqn')}",
                "file": af.get("file"),
                "line": af.get("start_line"),
            }
        elif call and callee:
            method_display = callee.get("name", "")
            if callee.get("kind") in ("Method", "Function"):
                method_display = f"{callee['name']}()"
            info.source = {
                "call_fqn": call.get("fqn"),
                "method_fqn": callee.get("fqn"),
                "method_name": method_display,
                "file": call.get("file"),
                "line": call.get("start_line"),
            }
    elif node.get("value_kind") == "result":
        # For result values: source is the producing Call directly
        result_source = data.get("result_source", {})
        call = result_source.get("call")
        callee = result_source.get("callee")
        if call and callee:
            method_display = callee.get("name", "")
            if callee.get("kind") in ("Method", "Function"):
                method_display = f"{callee['name']}()"
            info.source = {
                "call_fqn": call.get("fqn"),
                "method_fqn": callee.get("fqn"),
                "method_name": method_display,
                "file": call.get("file"),
                "line": call.get("start_line"),
            }

    # Scope: containing method/function from parent
    scope = data.get("scope")
    if scope and not info.declared_in:
        info.declared_in = {
            "fqn": scope.get("fqn"),
            "kind": scope.get("kind"),
            "file": scope.get("file"),
            "line": scope.get("start_line"),
        }
