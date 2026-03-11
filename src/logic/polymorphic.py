"""Polymorphic support: implementations and overrides for context trees.

Provides helpers to find implementing classes, override methods, and
interface method implementations for use in context orchestrators.

Key design rules:
- Implementation subtrees always use a FRESH cycle guard (not shared with main).
- For Class/Interface/Trait/Enum: returns direct implementors/extenders.
- For Method: returns override methods and interface method implementations.
"""

from __future__ import annotations

from typing import Callable

from ..db.query_runner import QueryRunner
from ..models.results import ContextEntry

# =============================================================================
# Cypher queries
# =============================================================================

_Q_CHILDREN = """
MATCH (n:Node {node_id: $id})<-[:EXTENDS|IMPLEMENTS]-(child)
RETURN child.node_id AS id, child.fqn AS fqn, child.kind AS kind,
       child.file AS file, child.start_line AS start_line,
       child.signature AS signature
"""

_Q_DIRECT_OVERRIDES = """
MATCH (m:Node {node_id: $id})<-[:OVERRIDES]-(child)
RETURN child.node_id AS id, child.fqn AS fqn, child.kind AS kind,
       child.file AS file, child.start_line AS start_line,
       child.signature AS signature
"""

_Q_CONTAINING_CLASS = """
MATCH (m:Node {node_id: $id})<-[:CONTAINS]-(parent)
RETURN parent.node_id AS id, parent.kind AS kind
"""

_Q_METHOD_IN_CLASS = """
MATCH (cls:Node {node_id: $class_id})-[:CONTAINS]->(method:Method)
WHERE method.name = $method_name
RETURN method.node_id AS id, method.fqn AS fqn, method.kind AS kind,
       method.file AS file, method.start_line AS start_line,
       method.signature AS signature
"""

_Q_INTERFACE_METHOD_IDS = """
MATCH (m:Node {node_id: $method_id})<-[:CONTAINS]-(cls)
MATCH (cls)-[:IMPLEMENTS]->(iface)-[:CONTAINS]->(iface_method:Method)
WHERE iface_method.name = m.name
RETURN iface_method.node_id AS id
"""

_Q_CONCRETE_IMPLEMENTORS_DIRECT = """
MATCH (m:Node {node_id: $method_id})<-[:CONTAINS]-(iface)
WHERE iface.kind = 'Interface'
MATCH (iface)<-[:IMPLEMENTS]-(impl_cls)
MATCH (impl_cls)-[:CONTAINS]->(impl_method:Method)
WHERE impl_method.name = m.name
RETURN impl_method.node_id AS id
ORDER BY impl_cls.node_id
"""

_Q_CONCRETE_IMPLEMENTORS_TRANSITIVE = """
MATCH (m:Node {node_id: $method_id})<-[:CONTAINS]-(iface)
WHERE iface.kind = 'Interface'
MATCH (iface)<-[:EXTENDS*]-(child_iface:Interface)<-[:IMPLEMENTS]-(impl_cls)
MATCH (impl_cls)-[:CONTAINS]->(impl_method:Method)
WHERE impl_method.name = m.name
RETURN impl_method.node_id AS id
ORDER BY impl_cls.node_id
"""


# =============================================================================
# Public helpers
# =============================================================================


def get_implementations_for_node(
    runner: QueryRunner,
    node: object,  # NodeData
    depth: int,
    max_depth: int,
    limit: int,
    visited: set[str],
    count: list[int],
    shown_impl_for: set[str],
    execution_flow_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Return implementing classes or override methods for a node.

    For Class/Interface/Trait/Enum nodes: returns direct implementors/extenders.
    For Method nodes: returns override methods + interface method implementations.

    Each implementation subtree uses a FRESH cycle guard to prevent false
    cycle detection from shared traversal state.

    Args:
        runner: Active QueryRunner.
        node: NodeData for the target node.
        depth: Current depth in the tree.
        max_depth: Maximum recursion depth.
        limit: Maximum total entries (checked via count[0]).
        visited: Global visited set for the main traversal (not modified here).
        count: Mutable single-element list tracking total entries emitted.
        shown_impl_for: Set of node_ids for which implementations were already shown.
        execution_flow_fn: Optional callable(runner, method_id, depth, ...) for
            recursing into method execution flow.

    Returns:
        List of ContextEntry objects for implementations.
    """
    node_id: str = node.node_id  # type: ignore[attr-defined]
    node_kind: str = node.kind  # type: ignore[attr-defined]

    if node_id in shown_impl_for:
        return []
    shown_impl_for.add(node_id)

    entries: list[ContextEntry] = []

    if node_kind in ("Class", "Interface", "Trait", "Enum"):
        # Return direct implementors / extenders
        records = runner.execute(_Q_CHILDREN, id=node_id)
        for r in records:
            if count[0] >= limit:
                break
            child_id = r["id"]
            # Fresh cycle guard per implementation subtree
            impl_cycle_guard: set[str] = set()
            impl_cycle_guard.add(child_id)
            entry = ContextEntry(
                depth=depth,
                node_id=child_id,
                fqn=r["fqn"] or "",
                kind=r.get("kind"),
                file=r.get("file"),
                line=r.get("start_line"),
                signature=r.get("signature"),
                ref_type="implements",
            )
            entries.append(entry)
            count[0] += 1

    elif node_kind in ("Method", "Function"):
        # Direct overrides
        override_records = runner.execute(_Q_DIRECT_OVERRIDES, id=node_id)
        seen_override_ids: set[str] = set()
        for r in override_records:
            if count[0] >= limit:
                break
            child_id = r["id"]
            if child_id in seen_override_ids:
                continue
            seen_override_ids.add(child_id)
            entry = ContextEntry(
                depth=depth,
                node_id=child_id,
                fqn=r["fqn"] or "",
                kind=r.get("kind"),
                file=r.get("file"),
                line=r.get("start_line"),
                signature=r.get("signature"),
                ref_type="overrides",
            )
            entries.append(entry)
            count[0] += 1

        # Interface method implementations: find classes implementing an
        # interface that declares a method with the same name, then look up
        # that method in implementing classes.
        iface_impl_records = list(runner.execute(
            _Q_CONCRETE_IMPLEMENTORS_DIRECT, method_id=node_id
        )) + list(runner.execute(
            _Q_CONCRETE_IMPLEMENTORS_TRANSITIVE, method_id=node_id
        ))
        for r in iface_impl_records:
            if count[0] >= limit:
                break
            impl_id = r["id"]
            if impl_id in seen_override_ids:
                continue
            seen_override_ids.add(impl_id)
            # We don't have full node data from this query; build minimal entry
            entry = ContextEntry(
                depth=depth,
                node_id=impl_id,
                fqn=impl_id,
                kind="Method",
                ref_type="implements",
            )
            entries.append(entry)
            count[0] += 1

    return entries


def get_interface_method_ids(runner: QueryRunner, method_id: str) -> list[str]:
    """Return node_ids of interface methods that the given method implements.

    Walks from the method's containing class up to any implemented interfaces
    and finds methods with the same name.

    Args:
        runner: Active QueryRunner.
        method_id: Node ID of the method.

    Returns:
        List of interface method node_ids (may be empty).
    """
    records = runner.execute(_Q_INTERFACE_METHOD_IDS, method_id=method_id)
    return [r["id"] for r in records if r["id"]]


def get_concrete_implementors(runner: QueryRunner, method_id: str) -> list[str]:
    """Return node_ids of concrete methods that implement this interface method.

    Starts from an interface method, finds the interface's implementing classes,
    then returns the matching method in each implementing class.

    Args:
        runner: Active QueryRunner.
        method_id: Node ID of the interface method.

    Returns:
        List of concrete method node_ids (may be empty).
    """
    # Direct implementors first, then transitive (via extends chain).
    # This matches kloc-cli's ordering: direct implementors in sot.json order,
    # followed by transitive implementors.
    direct = runner.execute(_Q_CONCRETE_IMPLEMENTORS_DIRECT, method_id=method_id)
    transitive = runner.execute(_Q_CONCRETE_IMPLEMENTORS_TRANSITIVE, method_id=method_id)
    seen: set[str] = set()
    result: list[str] = []
    for r in list(direct) + list(transitive):
        mid = r["id"]
        if mid and mid not in seen:
            seen.add(mid)
            result.append(mid)
    return result
