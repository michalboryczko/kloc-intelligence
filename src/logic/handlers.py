"""Strategy pattern handlers for USED BY edge classification.

Ported from kloc-cli's used_by_handlers.py. Each handler processes one
reference type (or group of related types) and appends context entry dicts
into the appropriate bucket in EntryBucket.

EdgeContext carries all pre-resolved information about a single edge so
handlers don't need to perform additional Neo4j queries.
"""

from dataclasses import dataclass, field
from typing import Protocol

from .graph_helpers import format_method_fqn


@dataclass(frozen=True)
class EdgeContext:
    """Immutable context for a single edge being classified.

    All data is pre-fetched from Neo4j before handler invocation.
    Handlers should NOT need to perform additional queries.
    """

    start_id: str  # Target node being queried (the "used by what?" subject)
    source_id: str  # Source of the edge (who uses the target)
    source_kind: str
    source_fqn: str
    source_name: str
    source_file: str | None
    source_start_line: int | None
    source_signature: str | None
    target_kind: str
    target_fqn: str
    target_name: str
    ref_type: str
    file: str | None  # Edge location file
    line: int | None  # Edge location line
    call_node_id: str | None
    classes_with_injection: frozenset[str]
    # Pre-fetched data for handlers
    containing_method_id: str | None = None
    containing_method_fqn: str | None = None
    containing_method_kind: str | None = None
    containing_class_id: str | None = None
    containing_class_fqn: str | None = None
    containing_class_kind: str | None = None
    containing_class_file: str | None = None
    containing_class_start_line: int | None = None
    call_kind: str | None = None
    access_chain: str | None = None
    on_kind: str | None = None
    arguments: tuple = ()
    # For PropertyTypeHandler: pre-resolved property data
    property_node_id: str | None = None
    property_fqn: str | None = None
    property_file: str | None = None
    property_start_line: int | None = None


@dataclass
class EntryBucket:
    """Mutable collector for classified USED BY entries with dedup tracking."""

    instantiation: list = field(default_factory=list)
    extends: list = field(default_factory=list)
    property_type: list = field(default_factory=list)
    method_call: list = field(default_factory=list)
    property_access_groups: dict = field(default_factory=dict)
    param_return: list = field(default_factory=list)

    # Dedup tracking
    seen_instantiation_methods: set = field(default_factory=set)
    seen_property_type_props: set = field(default_factory=set)


class UsedByHandler(Protocol):
    """Protocol for USED BY edge handlers."""

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        """Process an edge and append entries to the appropriate bucket."""
        ...


class InstantiationHandler:
    """Handle [instantiation] edges -- new ClassName() calls.

    Deduplicates by containing method: only one instantiation entry per
    method, even if the class is constructed multiple times in the same
    method.
    """

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        method_key = ctx.containing_method_id or ctx.source_id
        if method_key in bucket.seen_instantiation_methods:
            return
        bucket.seen_instantiation_methods.add(method_key)

        # Use containing method's FQN if available, otherwise source's
        if ctx.containing_method_id and ctx.containing_method_fqn:
            entry_fqn = format_method_fqn(ctx.containing_method_fqn, ctx.containing_method_kind or "")
            entry_kind = ctx.containing_method_kind or ctx.source_kind
            entry_node_id = ctx.containing_method_id
        else:
            entry_fqn = ctx.source_fqn
            entry_kind = ctx.source_kind
            entry_node_id = ctx.source_id

        entry = {
            "depth": 1,
            "node_id": entry_node_id,
            "fqn": entry_fqn,
            "kind": entry_kind,
            "file": ctx.file,
            "line": ctx.line,
            "ref_type": "instantiation",
            "children": [],
            "arguments": list(ctx.arguments),
        }
        bucket.instantiation.append(entry)


class ExtendsHandler:
    """Handle [extends] edges -- class inheritance.

    Creates a simple entry for the source class that extends the target.
    """

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        entry = {
            "depth": 1,
            "node_id": ctx.source_id,
            "fqn": ctx.source_fqn,
            "kind": ctx.source_kind,
            "file": ctx.source_file,
            "line": ctx.source_start_line,
            "ref_type": "extends",
            "children": [],
        }
        bucket.extends.append(entry)


class ImplementsHandler:
    """Handle [implements] edges -- interface implementation.

    Appends to the extends bucket (same display group as extends).
    """

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        entry = {
            "depth": 1,
            "node_id": ctx.source_id,
            "fqn": ctx.source_fqn,
            "kind": ctx.source_kind,
            "file": ctx.source_file,
            "line": ctx.source_start_line,
            "ref_type": "implements",
            "children": [],
        }
        bucket.extends.append(entry)


class PropertyTypeHandler:
    """Handle [property_type] edges -- typed property declarations.

    Deduplicates by property FQN. Uses pre-resolved property data from
    EdgeContext when the source is a Method (constructor promotion case).
    """

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        prop_fqn = None
        prop_node_id = None
        prop_file = None
        prop_start_line = None

        if ctx.source_kind == "Property":
            # Direct property source
            prop_fqn = ctx.source_fqn
            prop_node_id = ctx.source_id
            prop_file = ctx.source_file
            prop_start_line = ctx.source_start_line
        elif ctx.source_kind in ("Method", "Function"):
            # Constructor promotion: use pre-resolved property data
            prop_fqn = ctx.property_fqn
            prop_node_id = ctx.property_node_id
            prop_file = ctx.property_file
            prop_start_line = ctx.property_start_line

        if prop_fqn and prop_node_id and prop_fqn not in bucket.seen_property_type_props:
            bucket.seen_property_type_props.add(prop_fqn)
            entry = {
                "depth": 1,
                "node_id": prop_node_id,
                "fqn": prop_fqn,
                "kind": "Property",
                "file": prop_file,
                "line": prop_start_line,
                "ref_type": "property_type",
                "children": [],
            }
            bucket.property_type.append(entry)


class MethodCallHandler:
    """Handle [method_call] edges -- method invocations on injected properties.

    Suppresses method_call if the containing class has a property_type
    injection for this target class (those calls are shown at depth 2 instead).
    """

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        # Suppress method_call if class has injection
        if ctx.containing_class_id and ctx.containing_class_id in ctx.classes_with_injection:
            return

        # Use containing method's FQN if available
        if ctx.containing_method_id and ctx.containing_method_fqn:
            method_fqn = format_method_fqn(
                ctx.containing_method_fqn, ctx.containing_method_kind or ""
            )
            method_kind = ctx.containing_method_kind or ctx.source_kind
            method_node_id = ctx.containing_method_id
        else:
            method_fqn = ctx.source_fqn
            method_kind = ctx.source_kind
            method_node_id = ctx.source_id

        callee_name = None
        if ctx.target_kind == "Method":
            callee_name = ctx.target_name + "()"

        entry = {
            "depth": 1,
            "node_id": method_node_id,
            "fqn": method_fqn,
            "kind": method_kind,
            "file": ctx.file,
            "line": ctx.line,
            "ref_type": "method_call",
            "callee": callee_name,
            "on": ctx.access_chain,
            "on_kind": ctx.on_kind,
            "children": [],
            "arguments": list(ctx.arguments),
        }
        bucket.method_call.append(entry)


class PropertyAccessHandler:
    """Handle [property_access] edges -- grouped by property FQN + method + on_expr.

    Multiple accesses to the same property from the same method are grouped
    together with accumulated line numbers.
    """

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        prop_fqn = ctx.target_fqn

        # Use containing method's FQN if available
        if ctx.containing_method_id and ctx.containing_method_fqn:
            method_fqn = ctx.containing_method_fqn
            method_id = ctx.containing_method_id
            method_kind = ctx.containing_method_kind or ctx.source_kind
        else:
            method_fqn = ctx.source_fqn
            method_id = ctx.source_id
            method_kind = ctx.source_kind

        on_expr = ctx.access_chain
        on_kind = ctx.on_kind

        if prop_fqn not in bucket.property_access_groups:
            bucket.property_access_groups[prop_fqn] = []

        # Check if we already have a group entry for this method + on_expr + on_kind
        found = False
        for group_entry in bucket.property_access_groups[prop_fqn]:
            if (
                group_entry["method_fqn"] == method_fqn
                and group_entry["on_expr"] == on_expr
                and group_entry["on_kind"] == on_kind
            ):
                group_entry["lines"].append(ctx.line)
                found = True
                break

        if not found:
            bucket.property_access_groups[prop_fqn].append({
                "method_fqn": method_fqn,
                "method_id": method_id,
                "method_kind": method_kind,
                "lines": [ctx.line],
                "on_expr": on_expr,
                "on_kind": on_kind,
                "file": ctx.file,
            })


class ParamReturnHandler:
    """Handle [parameter_type], [return_type], [type_hint] edges.

    For return_type: shows method-level FQN. Deduplicates by method FQN.
    For parameter_type/type_hint: groups by containing class. Deduplicates
    by class FQN.
    """

    def handle(self, ctx: EdgeContext, bucket: EntryBucket) -> None:
        # For return_type, show method-level FQN instead of class-level
        if ctx.ref_type == "return_type" and ctx.source_kind in ("Method", "Function"):
            method_fqn = format_method_fqn(ctx.source_fqn, ctx.source_kind)
            already_exists = any(e["fqn"] == method_fqn for e in bucket.param_return)
            if not already_exists:
                entry = {
                    "depth": 1,
                    "node_id": ctx.source_id,
                    "fqn": method_fqn,
                    "kind": ctx.source_kind,
                    "file": ctx.source_file,
                    "line": ctx.source_start_line,
                    "signature": ctx.source_signature,
                    "ref_type": ctx.ref_type,
                    "children": [],
                }
                bucket.param_return.append(entry)
            return

        # Group by containing class for parameter_type and type_hint
        # Walk up containment to find the class
        cls_id = ctx.containing_class_id
        cls_fqn = None
        cls_kind = None
        cls_file = None
        cls_start_line = None

        if ctx.source_kind in ("Class", "Interface", "Trait", "Enum"):
            # Source IS the class
            cls_id = ctx.source_id
            cls_fqn = ctx.source_fqn
            cls_kind = ctx.source_kind
            cls_file = ctx.source_file
            cls_start_line = ctx.source_start_line
        elif cls_id is not None:
            # Pre-resolved containing class from Q2 query
            cls_fqn = ctx.containing_class_fqn
            cls_kind = ctx.containing_class_kind
            cls_file = ctx.containing_class_file
            cls_start_line = ctx.containing_class_start_line

        if cls_id is None:
            # No containing class found; skip
            return

        # Deduplicate by class FQN
        check_fqn = cls_fqn or ctx.source_fqn
        already_exists = any(e["fqn"] == check_fqn for e in bucket.param_return)
        if already_exists:
            return

        entry = {
            "depth": 1,
            "node_id": cls_id,
            "fqn": check_fqn,
            "kind": cls_kind or ctx.source_kind,
            "file": cls_file or ctx.source_file,
            "line": cls_start_line if cls_start_line is not None else ctx.source_start_line,
            "ref_type": ctx.ref_type,
            "children": [],
        }
        bucket.param_return.append(entry)


# Handler registry: maps ref_type to handler instance
_param_return_handler = ParamReturnHandler()

USED_BY_HANDLERS: dict[str, UsedByHandler] = {
    "instantiation": InstantiationHandler(),
    "extends": ExtendsHandler(),
    "implements": ImplementsHandler(),
    "property_type": PropertyTypeHandler(),
    "method_call": MethodCallHandler(),
    "property_access": PropertyAccessHandler(),
    "parameter_type": _param_return_handler,
    "return_type": _param_return_handler,
    "type_hint": _param_return_handler,
}
