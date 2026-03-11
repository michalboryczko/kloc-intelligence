"""Cypher queries and execution functions for usages (incoming USES edges)."""

from ..query_runner import QueryRunner
from ..result_mapper import record_to_node
from ...models.node import NodeData

# Container kinds that support member expansion
CONTAINER_KINDS = ("Class", "Interface", "Trait", "Enum", "File")

# Direct usages: find all sources that USE this target
USAGES_DIRECT = """
MATCH (target:Node {node_id: $id})<-[e:USES]-(source:Node)
RETURN source, e, e.loc_file AS loc_file, e.loc_line AS loc_line
ORDER BY e.loc_file, e.loc_line
"""

# Container member expansion: find all usages of the target + its contained members
USAGES_WITH_MEMBERS = """
MATCH (target:Node {node_id: $id})
WHERE target.kind IN $container_kinds
OPTIONAL MATCH (target)-[:CONTAINS*]->(member)
WITH target, collect(DISTINCT member) + [target] AS all_nodes
UNWIND all_nodes AS node
MATCH (node)<-[e:USES]-(source:Node)
RETURN DISTINCT source, e, e.loc_file AS loc_file, e.loc_line AS loc_line
ORDER BY e.loc_file, e.loc_line
LIMIT $limit
"""

# Fetch a single node by ID
FETCH_NODE = """
MATCH (n:Node {node_id: $id})
RETURN n
"""


def fetch_node(runner: QueryRunner, node_id: str) -> NodeData | None:
    """Fetch a single node by ID."""
    record = runner.execute_single(FETCH_NODE, id=node_id)
    if record is None:
        return None
    return record_to_node(record)


def query_usages_direct(runner: QueryRunner, node_id: str) -> list[dict]:
    """Query direct incoming USES edges for a node.

    Returns list of dicts with keys: source_id, source_fqn, loc_file, loc_line,
    source_file, source_start_line.
    """
    records = runner.execute(USAGES_DIRECT, id=node_id)
    results = []
    for record in records:
        source = record["source"]
        results.append({
            "source_id": source["node_id"],
            "source_fqn": source["fqn"],
            "loc_file": record["loc_file"],
            "loc_line": record["loc_line"],
            "source_file": source.get("file"),
            "source_start_line": source.get("start_line"),
        })
    return results


def query_usages_with_members(
    runner: QueryRunner, node_id: str, limit: int = 100
) -> list[dict]:
    """Query usages including member expansion for container nodes.

    Returns list of dicts with keys: source_id, source_fqn, loc_file, loc_line,
    source_file, source_start_line.
    """
    records = runner.execute(
        USAGES_WITH_MEMBERS,
        id=node_id,
        container_kinds=list(CONTAINER_KINDS),
        limit=limit,
    )
    results = []
    seen_sources = set()
    for record in records:
        source = record["source"]
        source_id = source["node_id"]
        if source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        results.append({
            "source_id": source_id,
            "source_fqn": source["fqn"],
            "loc_file": record["loc_file"],
            "loc_line": record["loc_line"],
            "source_file": source.get("file"),
            "source_start_line": source.get("start_line"),
        })
    return results


def query_usages_for_node(
    runner: QueryRunner,
    node_id: str,
    include_members: bool = True,
    limit: int = 100,
) -> list[dict]:
    """Query usages for a node, with optional member expansion.

    For container nodes (Class, Interface, Trait, Enum, File), if include_members
    is True, also finds usages of contained members.
    """
    if include_members:
        # Try member expansion first; if node is not a container, the query
        # will return empty. In that case, fall back to direct.
        results = query_usages_with_members(runner, node_id, limit=limit)
        if results:
            return results
    return query_usages_direct(runner, node_id)
