"""Usages orchestrator: resolve symbol -> query -> build tree result."""

from ..db.query_runner import QueryRunner
from ..db.queries.resolve import resolve_symbol
from ..db.queries.usages import (
    fetch_node,
    query_usages_for_node,
    query_usages_direct,
)
from ..models.node import NodeData
from ..models.results import UsageEntry, UsagesTreeResult


def run_usages(
    runner: QueryRunner,
    query: str,
    depth: int = 1,
    limit: int = 100,
) -> UsagesTreeResult:
    """Resolve a symbol and find its usages with BFS tree expansion.

    Args:
        runner: QueryRunner connected to Neo4j.
        query: Symbol query string (FQN, partial, etc.).
        depth: BFS depth for expansion (1 = direct only).
        limit: Maximum total results across all depths.

    Returns:
        UsagesTreeResult with tree structure.

    Raises:
        ValueError: If symbol cannot be resolved.
    """
    # 1. Resolve symbol
    candidates = resolve_symbol(runner, query)
    if not candidates:
        raise ValueError(f"Symbol not found: {query}")

    target = candidates[0]
    return _build_usages_tree(runner, target, depth, limit)


def run_usages_by_id(
    runner: QueryRunner,
    node_id: str,
    depth: int = 1,
    limit: int = 100,
) -> UsagesTreeResult:
    """Find usages for a node by ID with BFS tree expansion.

    Args:
        runner: QueryRunner connected to Neo4j.
        node_id: Node ID to find usages for.
        depth: BFS depth for expansion (1 = direct only).
        limit: Maximum total results across all depths.

    Returns:
        UsagesTreeResult with tree structure.

    Raises:
        ValueError: If node not found.
    """
    target = fetch_node(runner, node_id)
    if not target:
        raise ValueError(f"Node not found: {node_id}")

    return _build_usages_tree(runner, target, depth, limit)


def _build_usages_tree(
    runner: QueryRunner,
    target: NodeData,
    depth: int,
    limit: int,
) -> UsagesTreeResult:
    """Build BFS tree of usages matching kloc-cli behavior.

    Key behaviors:
    - Global visited set: node visited at depth 1 is NOT revisited at depth 2
    - Global count for limit enforcement across all depths
    - Container member expansion at FIRST depth level only
    - For depth>1, use direct USES queries per node (no member expansion)
    """
    visited: set[str] = {target.node_id}
    count = [0]

    def build_tree(current_id: str, current_depth: int, is_root: bool = False) -> list[UsageEntry]:
        if current_depth > depth or count[0] >= limit:
            return []

        entries = []

        # Member expansion only for the root target (first depth level)
        if is_root:
            edges = query_usages_for_node(
                runner, current_id, include_members=True, limit=limit
            )
        else:
            edges = query_usages_direct(runner, current_id)

        for edge in edges:
            source_id = edge["source_id"]
            if source_id in visited:
                continue
            visited.add(source_id)

            if count[0] >= limit:
                break
            count[0] += 1

            # Location: edge location preferred, fallback to source node
            loc_file = edge["loc_file"]
            loc_line = edge["loc_line"]
            if loc_file is None:
                loc_file = edge["source_file"]
                loc_line = edge["source_start_line"]

            entry = UsageEntry(
                depth=current_depth,
                node_id=source_id,
                fqn=edge["source_fqn"],
                file=loc_file,
                line=loc_line,
                children=[],
            )

            # Recurse for children
            if current_depth < depth:
                entry.children = build_tree(source_id, current_depth + 1)

            entries.append(entry)

        return entries

    tree = build_tree(target.node_id, 1, is_root=True)

    return UsagesTreeResult(
        target=target,
        max_depth=depth,
        tree=tree,
    )
