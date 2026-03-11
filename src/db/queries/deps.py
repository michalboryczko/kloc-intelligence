"""Cypher queries and execution functions for deps (outgoing USES edges)."""

from ..query_runner import QueryRunner

# Container kinds that support member expansion
CONTAINER_KINDS = ("Class", "Interface", "Trait", "Enum", "File")

# Direct deps: find all targets that this source USES
DEPS_DIRECT = """
MATCH (source:Node {node_id: $id})-[e:USES]->(target:Node)
RETURN target, e, e.loc_file AS loc_file, e.loc_line AS loc_line
ORDER BY e.loc_file, e.loc_line
"""

# Container member expansion: find all deps from the source + its contained members
DEPS_WITH_MEMBERS = """
MATCH (source:Node {node_id: $id})
WHERE source.kind IN $container_kinds
OPTIONAL MATCH (source)-[:CONTAINS*]->(member)
WITH source, collect(DISTINCT member) + [source] AS all_nodes
UNWIND all_nodes AS node
MATCH (node)-[e:USES]->(target:Node)
RETURN DISTINCT target, e, e.loc_file AS loc_file, e.loc_line AS loc_line
ORDER BY e.loc_file, e.loc_line
LIMIT $limit
"""


def query_deps_direct(runner: QueryRunner, node_id: str) -> list[dict]:
    """Query direct outgoing USES edges from a node.

    Returns list of dicts with keys: target_id, target_fqn, loc_file, loc_line,
    target_file, target_start_line.
    """
    records = runner.execute(DEPS_DIRECT, id=node_id)
    results = []
    for record in records:
        target = record["target"]
        results.append({
            "target_id": target["node_id"],
            "target_fqn": target["fqn"],
            "loc_file": record["loc_file"],
            "loc_line": record["loc_line"],
            "target_file": target.get("file"),
            "target_start_line": target.get("start_line"),
        })
    return results


def query_deps_with_members(
    runner: QueryRunner, node_id: str, limit: int = 100
) -> list[dict]:
    """Query deps including member expansion for container nodes.

    Returns list of dicts with keys: target_id, target_fqn, loc_file, loc_line,
    target_file, target_start_line.
    """
    records = runner.execute(
        DEPS_WITH_MEMBERS,
        id=node_id,
        container_kinds=list(CONTAINER_KINDS),
        limit=limit,
    )
    results = []
    seen_targets = set()
    for record in records:
        target = record["target"]
        target_id = target["node_id"]
        if target_id in seen_targets:
            continue
        seen_targets.add(target_id)
        results.append({
            "target_id": target_id,
            "target_fqn": target["fqn"],
            "loc_file": record["loc_file"],
            "loc_line": record["loc_line"],
            "target_file": target.get("file"),
            "target_start_line": target.get("start_line"),
        })
    return results


def query_deps_for_node(
    runner: QueryRunner,
    node_id: str,
    include_members: bool = True,
    limit: int = 100,
) -> list[dict]:
    """Query deps for a node, with optional member expansion.

    For container nodes (Class, Interface, Trait, Enum, File), if include_members
    is True, also finds deps from contained members.
    """
    if include_members:
        results = query_deps_with_members(runner, node_id, limit=limit)
        if results:
            return results
    return query_deps_direct(runner, node_id)
