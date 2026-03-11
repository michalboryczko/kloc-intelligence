"""Deps orchestrator: resolve symbol -> query -> build tree result."""

from ..db.query_runner import QueryRunner
from ..db.queries.resolve import resolve_symbol
from ..db.queries.deps import (
    query_deps_for_node,
    query_deps_direct,
)
from ..db.queries.usages import fetch_node
from ..models.node import NodeData
from ..models.results import DepsEntry, DepsTreeResult


def run_deps(
    runner: QueryRunner,
    query: str,
    depth: int = 1,
    limit: int = 100,
) -> DepsTreeResult:
    """Resolve a symbol and find its dependencies with BFS tree expansion.

    Args:
        runner: QueryRunner connected to Neo4j.
        query: Symbol query string (FQN, partial, etc.).
        depth: BFS depth for expansion (1 = direct only).
        limit: Maximum total results across all depths.

    Returns:
        DepsTreeResult with tree structure.

    Raises:
        ValueError: If symbol cannot be resolved.
    """
    candidates = resolve_symbol(runner, query)
    if not candidates:
        raise ValueError(f"Symbol not found: {query}")

    target = candidates[0]
    return _build_deps_tree(runner, target, depth, limit)


def run_deps_by_id(
    runner: QueryRunner,
    node_id: str,
    depth: int = 1,
    limit: int = 100,
) -> DepsTreeResult:
    """Find dependencies for a node by ID with BFS tree expansion.

    Args:
        runner: QueryRunner connected to Neo4j.
        node_id: Node ID to find dependencies for.
        depth: BFS depth for expansion (1 = direct only).
        limit: Maximum total results across all depths.

    Returns:
        DepsTreeResult with tree structure.

    Raises:
        ValueError: If node not found.
    """
    target = fetch_node(runner, node_id)
    if not target:
        raise ValueError(f"Node not found: {node_id}")

    return _build_deps_tree(runner, target, depth, limit)


def _build_deps_tree(
    runner: QueryRunner,
    target: NodeData,
    depth: int,
    limit: int,
) -> DepsTreeResult:
    """Build BFS tree of dependencies matching kloc-cli behavior.

    Key behaviors:
    - Global visited set: node visited at depth 1 is NOT revisited at depth 2
    - Global count for limit enforcement across all depths
    - Container member expansion at FIRST depth level only
    - For depth>1, use direct USES queries per node (no member expansion)
    - Location: edge loc preferred; fallback to dep_node.file but line=None
    """
    visited: set[str] = {target.node_id}
    count = [0]

    def build_tree(current_id: str, current_depth: int, is_root: bool = False) -> list[DepsEntry]:
        if current_depth > depth or count[0] >= limit:
            return []

        entries = []

        # Member expansion only for the root target (first depth level)
        if is_root:
            edges = query_deps_for_node(
                runner, current_id, include_members=True, limit=limit
            )
        else:
            edges = query_deps_direct(runner, current_id)

        for edge in edges:
            dep_id = edge["target_id"]
            if dep_id in visited:
                continue
            visited.add(dep_id)

            if count[0] >= limit:
                break
            count[0] += 1

            # Location: edge loc preferred; fallback to target file, but line=None
            loc_file = edge["loc_file"]
            loc_line = edge["loc_line"]
            if loc_file is None:
                loc_file = edge["target_file"]
                loc_line = None  # Per kloc-cli: no target node line fallback for deps

            entry = DepsEntry(
                depth=current_depth,
                node_id=dep_id,
                fqn=edge["target_fqn"],
                file=loc_file,
                line=loc_line,
                children=[],
            )

            # Recurse for children
            if current_depth < depth:
                entry.children = build_tree(dep_id, current_depth + 1)

            entries.append(entry)

        return entries

    tree = build_tree(target.node_id, 1, is_root=True)

    return DepsTreeResult(
        target=target,
        max_depth=depth,
        tree=tree,
    )
