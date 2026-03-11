"""Context orchestrator: wires all context sub-modules together.

The main entry point is execute_context(), which:
1. Resolves the symbol via resolve_symbol()
2. Fetches the definition via build_definition()
3. Dispatches to kind-specific USED BY handlers
4. Dispatches to kind-specific USES handlers
5. Returns a ContextResult

Kind-based dispatch follows the reference implementation exactly:
- USED BY: Value, Property, Class, Interface, Method(__construct), generic
- USES: Method/Function, Value, Property, Class, Interface, generic

Deferred callback wiring breaks circular deps between execution_flow
and implementations modules.
"""

from __future__ import annotations

from ..db.query_runner import QueryRunner
from ..db.queries.resolve import resolve_symbol
from ..db.queries.definition import fetch_definition_data
from ..logic.definition import build_definition
from ..models.node import NodeData
from ..models.results import ContextResult, ContextEntry

from .class_context import (
    build_class_used_by,
    build_class_uses,
    build_caller_chain_for_method,
)
from .interface_context import (
    build_interface_used_by,
    build_interface_uses,
)
from .method_context import (
    build_execution_flow,
    get_type_references,
)
from .generic_context import build_generic_used_by
from .value_context import (
    build_value_consumer_chain,
    build_value_source_chain,
)
from .property_context import (
    build_property_used_by,
    build_property_uses,
)
from ..logic.polymorphic import (
    get_concrete_implementors,
)


# ========================================================================
# Containing class resolution (ISSUE-A: constructor redirect)
# ========================================================================

_Q_CONTAINING_CLASS = """
MATCH (m:Node {node_id: $method_id})<-[:CONTAINS]-(parent)
RETURN parent.node_id AS id, parent.kind AS kind
"""

_Q_NODE_BY_ID = """
MATCH (n:Node {node_id: $node_id})
RETURN n
"""


def _fetch_node(runner: QueryRunner, node_id: str) -> NodeData | None:
    """Fetch a single NodeData by node_id from Neo4j."""
    from ..db.result_mapper import record_to_node
    record = runner.execute_single(_Q_NODE_BY_ID, node_id=node_id)
    if record and record["n"]:
        return record_to_node(record)
    return None


# ========================================================================
# Context orchestrator
# ========================================================================


def execute_context(
    runner: QueryRunner,
    symbol: str,
    depth: int = 1,
    limit: int = 100,
    include_impl: bool = False,
    direct_only: bool = False,
    with_imports: bool = False,
) -> ContextResult:
    """Execute a full context query: resolve + definition + used_by + uses.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        symbol: Symbol string to resolve.
        depth: BFS depth for expansion (default 1).
        limit: Maximum results per direction (default 100).
        include_impl: Include polymorphic analysis.
        direct_only: USED BY shows only direct references.
        with_imports: Include PHP import statements.

    Returns:
        ContextResult with target, definition, used_by, uses.

    Raises:
        ValueError: If symbol cannot be resolved.
    """
    # 1. Resolve symbol
    candidates = resolve_symbol(runner, symbol)
    if not candidates:
        raise ValueError(f"Symbol not found: {symbol}")
    target = candidates[0]

    # 2. Build definition
    def_data = fetch_definition_data(runner, target.node_id)
    definition = build_definition(def_data)

    # 3. Build USED BY tree
    used_by = _build_incoming_tree(
        runner, target, depth, limit, include_impl, direct_only, with_imports
    )

    # 4. Build USES tree
    uses = _build_outgoing_tree(
        runner, target, depth, limit, include_impl
    )

    return ContextResult(
        target=target,
        max_depth=depth,
        used_by=used_by,
        uses=uses,
        definition=definition,
    )


# ========================================================================
# USED BY dispatch
# ========================================================================


def _build_incoming_tree(
    runner: QueryRunner,
    target: NodeData,
    max_depth: int,
    limit: int,
    include_impl: bool = False,
    direct_only: bool = False,
    with_imports: bool = False,
) -> list[ContextEntry]:
    """Build USED BY section with kind-based dispatch."""
    kind = target.kind

    # Value nodes: dedicated consumer chain traversal
    if kind == "Value":
        return build_value_consumer_chain(
            runner, target.node_id, 1, max_depth, limit, visited=set()
        )

    # Property nodes: property USED BY
    if kind == "Property":
        return build_property_used_by(
            runner,
            target.node_id,
            target.name,
            target.fqn,
            1,
            max_depth,
            limit,
            caller_chain_fn=lambda mid, d, md: build_caller_chain_for_method(
                runner, mid, d, md, visited=set()
            ),
        )

    # Class nodes: class USED BY
    if kind == "Class":
        return build_class_used_by(runner, target, max_depth, limit)

    # Interface nodes: interface USED BY
    if kind == "Interface":
        return build_interface_used_by(runner, target, max_depth, limit)

    # ISSUE-A: Constructor redirect
    if kind == "Method" and target.name == "__construct":
        parent_rec = runner.execute_single(
            _Q_CONTAINING_CLASS, method_id=target.node_id
        )
        if parent_rec:
            parent_id = parent_rec["id"]
            parent_kind = parent_rec["kind"]
            if parent_kind in ("Class", "Enum"):
                parent_node = _fetch_node(runner, parent_id)
                if parent_node:
                    return build_class_used_by(
                        runner, parent_node, max_depth, limit
                    )

    # Method / Function: generic USED BY (method-level format with member_ref)
    if kind in ("Method", "Function"):
        return build_generic_used_by(runner, target, max_depth, limit)

    # Generic fallback (Trait, Enum, Const, etc.)
    return build_generic_used_by(runner, target, max_depth, limit)


# ========================================================================
# USES dispatch
# ========================================================================


def _build_outgoing_tree(
    runner: QueryRunner,
    target: NodeData,
    max_depth: int,
    limit: int,
    include_impl: bool = False,
) -> list[ContextEntry]:
    """Build USES section with kind-based dispatch."""
    kind = target.kind

    # Method / Function: type references + execution flow
    if kind in ("Method", "Function"):
        cycle_guard: set[str] = {target.node_id}
        count: list[int] = [0]

        # Get structural type references
        type_entries = get_type_references(
            runner, target.node_id, 1, cycle_guard, count, limit
        )

        # Get execution flow
        call_entries = build_execution_flow(
            runner, target.node_id, 1, max_depth, limit,
            cycle_guard, count,
        )

        # Interface -> concrete direction for USES
        impl_entries: list[ContextEntry] = []
        if include_impl and not call_entries:
            concrete_ids = get_concrete_implementors(runner, target.node_id)
            for concrete_id in concrete_ids:
                concrete_node = _fetch_node(runner, concrete_id)
                if not concrete_node:
                    continue
                impl_cycle_guard: set[str] = {concrete_id}
                impl_count: list[int] = [0]
                impl_type_entries = get_type_references(
                    runner, concrete_id, 1, impl_cycle_guard, impl_count, limit
                )
                impl_call_entries = build_execution_flow(
                    runner, concrete_id, 1, max_depth, limit,
                    impl_cycle_guard, impl_count,
                )
                impl_children = impl_type_entries + impl_call_entries
                concrete_fqn = concrete_node.fqn
                if concrete_node.kind == "Method" and not concrete_fqn.endswith("()"):
                    concrete_fqn += "()"
                impl_entry = ContextEntry(
                    depth=0,
                    node_id=concrete_id,
                    fqn=concrete_fqn,
                    kind=concrete_node.kind,
                    file=concrete_node.file,
                    line=concrete_node.start_line,
                    signature=concrete_node.signature,
                    children=impl_children,
                    via_interface=True,
                )
                impl_entries.append(impl_entry)

        return type_entries + call_entries + impl_entries

    # Value nodes: source chain
    if kind == "Value":
        return build_value_source_chain(
            runner, target.node_id, 1, max_depth, limit, visited=set()
        )

    # Property nodes: property USES
    if kind == "Property":
        return build_property_uses(
            runner,
            target.node_id,
            target.name,
            target.fqn,
            1,
            max_depth,
            limit,
        )

    # Class nodes: class USES
    if kind == "Class":
        return build_class_uses(runner, target, max_depth, limit)

    # Interface nodes: interface USES
    if kind == "Interface":
        return build_interface_uses(
            runner, target, max_depth, limit, include_impl
        )

    # Generic fallback: empty for now
    return []
