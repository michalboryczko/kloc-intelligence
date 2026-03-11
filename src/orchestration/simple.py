"""Orchestrators for owners, inherit, and overrides commands."""

from collections import deque

from ..db.query_runner import QueryRunner
from ..db.queries.resolve import resolve_symbol
from ..db.queries.owners import fetch_node, get_owners_chain
from ..db.queries.inherit import (
    INHERITABLE_KINDS,
    query_inherit_neighbors,
)
from ..db.queries.overrides import query_override_neighbors
from ..models.node import NodeData
from ..models.results import (
    OwnersResult,
    InheritEntry,
    InheritTreeResult,
    OverrideEntry,
    OverridesTreeResult,
)


# --- Owners ---


def run_owners(runner: QueryRunner, query: str) -> OwnersResult:
    """Resolve a symbol and return its containment chain.

    Args:
        runner: QueryRunner connected to Neo4j.
        query: Symbol query string (FQN, partial, etc.).

    Returns:
        OwnersResult with chain from target up to File root.

    Raises:
        ValueError: If symbol cannot be resolved.
    """
    candidates = resolve_symbol(runner, query)
    if not candidates:
        raise ValueError(f"Symbol not found: {query}")

    target = candidates[0]
    return _build_owners(runner, target.node_id)


def run_owners_by_id(runner: QueryRunner, node_id: str) -> OwnersResult:
    """Return the containment chain for a node by ID.

    Args:
        runner: QueryRunner connected to Neo4j.
        node_id: Node ID to find owners for.

    Returns:
        OwnersResult with chain from target up to File root.

    Raises:
        ValueError: If node not found.
    """
    target = fetch_node(runner, node_id)
    if not target:
        raise ValueError(f"Node not found: {node_id}")

    return _build_owners(runner, node_id)


def _build_owners(runner: QueryRunner, node_id: str) -> OwnersResult:
    """Build the containment chain for a node."""
    chain = get_owners_chain(runner, node_id)
    return OwnersResult(chain=chain)


# --- Inherit ---


def run_inherit(
    runner: QueryRunner,
    query: str,
    direction: str = "up",
    depth: int = 5,
    limit: int = 100,
) -> InheritTreeResult:
    """Resolve a symbol and find its inheritance tree.

    Args:
        runner: QueryRunner connected to Neo4j.
        query: Symbol query string (FQN, partial, etc.).
        direction: "up" for ancestors, "down" for descendants.
        depth: Maximum BFS depth.
        limit: Maximum total results.

    Returns:
        InheritTreeResult with tree structure.

    Raises:
        ValueError: If symbol cannot be resolved or is not an inheritable kind.
    """
    candidates = resolve_symbol(runner, query)
    if not candidates:
        raise ValueError(f"Symbol not found: {query}")

    target = candidates[0]
    if target.kind not in INHERITABLE_KINDS:
        raise ValueError(
            f"Node must be Class/Interface/Trait/Enum, got: {target.kind}"
        )

    return _build_inherit_tree(runner, target, direction, depth, limit)


def run_inherit_by_id(
    runner: QueryRunner,
    node_id: str,
    direction: str = "up",
    depth: int = 5,
    limit: int = 100,
) -> InheritTreeResult:
    """Find the inheritance tree for a node by ID.

    Args:
        runner: QueryRunner connected to Neo4j.
        node_id: Node ID to find inheritance for.
        direction: "up" for ancestors, "down" for descendants.
        depth: Maximum BFS depth.
        limit: Maximum total results.

    Returns:
        InheritTreeResult with tree structure.

    Raises:
        ValueError: If node not found or not an inheritable kind.
    """
    target = fetch_node(runner, node_id)
    if not target:
        raise ValueError(f"Node not found: {node_id}")

    if target.kind not in INHERITABLE_KINDS:
        raise ValueError(
            f"Node must be Class/Interface/Trait/Enum, got: {target.kind}"
        )

    return _build_inherit_tree(runner, target, direction, depth, limit)


def _build_inherit_tree(
    runner: QueryRunner,
    target: NodeData,
    direction: str,
    depth: int,
    limit: int,
) -> InheritTreeResult:
    """Build BFS tree of inheritance matching kloc-cli behavior.

    Uses a BFS with deque, global visited set, and global count limit.
    """
    visited: set[str] = {target.node_id}
    count = [0]

    # Seed the BFS queue with direct neighbors
    queue: deque[tuple[str, int, InheritEntry | None]] = deque()
    tree: list[InheritEntry] = []

    neighbors = query_inherit_neighbors(runner, target.node_id, direction)
    for n in neighbors:
        nid = n["node_id"]
        if nid not in visited:
            queue.append((nid, 1, None))

    while queue and count[0] < limit:
        current_id, current_depth, parent_entry = queue.popleft()
        if current_id in visited:
            continue
        visited.add(current_id)

        node = fetch_node(runner, current_id)
        if not node:
            continue
        count[0] += 1

        entry = InheritEntry(
            depth=current_depth,
            node_id=node.node_id,
            fqn=node.fqn,
            kind=node.kind,
            file=node.file,
            line=node.start_line,
            children=[],
        )

        if parent_entry is None:
            tree.append(entry)
        else:
            parent_entry.children.append(entry)

        # Continue BFS if within depth
        if current_depth < depth:
            next_neighbors = query_inherit_neighbors(
                runner, current_id, direction
            )
            for nn in next_neighbors:
                next_id = nn["node_id"]
                if next_id not in visited:
                    queue.append((next_id, current_depth + 1, entry))

    return InheritTreeResult(
        root=target,
        direction=direction,
        max_depth=depth,
        tree=tree,
    )


# --- Overrides ---


def run_overrides(
    runner: QueryRunner,
    query: str,
    direction: str = "up",
    depth: int = 5,
    limit: int = 100,
) -> OverridesTreeResult:
    """Resolve a symbol and find its override chain.

    Args:
        runner: QueryRunner connected to Neo4j.
        query: Symbol query string (FQN, partial, etc.).
        direction: "up" for parent methods, "down" for overriding methods.
        depth: Maximum BFS depth.
        limit: Maximum total results.

    Returns:
        OverridesTreeResult with tree structure.

    Raises:
        ValueError: If symbol cannot be resolved or is not a Method.
    """
    candidates = resolve_symbol(runner, query)
    if not candidates:
        raise ValueError(f"Symbol not found: {query}")

    target = candidates[0]
    if target.kind != "Method":
        raise ValueError(f"Node must be Method, got: {target.kind}")

    return _build_overrides_tree(runner, target, direction, depth, limit)


def run_overrides_by_id(
    runner: QueryRunner,
    node_id: str,
    direction: str = "up",
    depth: int = 5,
    limit: int = 100,
) -> OverridesTreeResult:
    """Find the override chain for a node by ID.

    Args:
        runner: QueryRunner connected to Neo4j.
        node_id: Node ID to find overrides for.
        direction: "up" for parent methods, "down" for overriding methods.
        depth: Maximum BFS depth.
        limit: Maximum total results.

    Returns:
        OverridesTreeResult with tree structure.

    Raises:
        ValueError: If node not found or not a Method.
    """
    target = fetch_node(runner, node_id)
    if not target:
        raise ValueError(f"Node not found: {node_id}")

    if target.kind != "Method":
        raise ValueError(f"Node must be Method, got: {target.kind}")

    return _build_overrides_tree(runner, target, direction, depth, limit)


def _build_overrides_tree(
    runner: QueryRunner,
    target: NodeData,
    direction: str,
    depth: int,
    limit: int,
) -> OverridesTreeResult:
    """Build BFS tree of overrides matching kloc-cli behavior.

    Uses a BFS with deque, global visited set, and global count limit.
    """
    visited: set[str] = {target.node_id}
    count = [0]

    queue: deque[tuple[str, int, OverrideEntry | None]] = deque()
    tree: list[OverrideEntry] = []

    neighbors = query_override_neighbors(runner, target.node_id, direction)
    for n in neighbors:
        nid = n["node_id"]
        if nid not in visited:
            queue.append((nid, 1, None))

    while queue and count[0] < limit:
        current_id, current_depth, parent_entry = queue.popleft()
        if current_id in visited:
            continue
        visited.add(current_id)

        node = fetch_node(runner, current_id)
        if not node:
            continue
        count[0] += 1

        entry = OverrideEntry(
            depth=current_depth,
            node_id=node.node_id,
            fqn=node.fqn,
            file=node.file,
            line=node.start_line,
            children=[],
        )

        if parent_entry is None:
            tree.append(entry)
        else:
            parent_entry.children.append(entry)

        # Continue BFS if within depth
        if current_depth < depth:
            next_neighbors = query_override_neighbors(
                runner, current_id, direction
            )
            for nn in next_neighbors:
                next_id = nn["node_id"]
                if next_id not in visited:
                    queue.append((next_id, current_depth + 1, entry))

    return OverridesTreeResult(
        root=target,
        direction=direction,
        max_depth=depth,
        tree=tree,
    )
