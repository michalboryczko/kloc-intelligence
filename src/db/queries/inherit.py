"""Cypher queries and execution functions for inherit (EXTENDS/IMPLEMENTS edges)."""

from ..query_runner import QueryRunner

# Inheritance kinds that can participate in inheritance
INHERITABLE_KINDS = ("Class", "Interface", "Trait", "Enum")

# Find parents: nodes that this node extends or implements
INHERIT_UP = """
MATCH (n:Node {node_id: $id})-[:EXTENDS|IMPLEMENTS]->(parent:Node)
RETURN parent
"""

# Find children: nodes that extend or implement this node
INHERIT_DOWN = """
MATCH (n:Node {node_id: $id})<-[:EXTENDS|IMPLEMENTS]-(child:Node)
RETURN child
"""


def query_inherit_neighbors(
    runner: QueryRunner, node_id: str, direction: str
) -> list[dict]:
    """Query direct inheritance neighbors for a node.

    Args:
        runner: QueryRunner connected to Neo4j.
        node_id: Node ID to query.
        direction: "up" for parents, "down" for children.

    Returns:
        List of dicts with keys: node_id, fqn, kind, file, start_line.
    """
    query = INHERIT_UP if direction == "up" else INHERIT_DOWN
    key = "parent" if direction == "up" else "child"
    records = runner.execute(query, id=node_id)
    results = []
    for record in records:
        node = record[key]
        results.append({
            "node_id": node["node_id"],
            "fqn": node["fqn"],
            "kind": node["kind"],
            "file": node.get("file"),
            "start_line": node.get("start_line"),
        })
    return results
