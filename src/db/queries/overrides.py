"""Cypher queries and execution functions for overrides (OVERRIDES edges)."""

from ..query_runner import QueryRunner

# Find the method(s) that this method overrides (parent methods)
OVERRIDES_UP = """
MATCH (n:Node {node_id: $id})-[:OVERRIDES]->(parent:Node)
RETURN parent
"""

# Find methods that override this method (child methods)
OVERRIDES_DOWN = """
MATCH (n:Node {node_id: $id})<-[:OVERRIDES]-(child:Node)
RETURN child
"""


def query_override_neighbors(
    runner: QueryRunner, node_id: str, direction: str
) -> list[dict]:
    """Query direct override neighbors for a method node.

    Args:
        runner: QueryRunner connected to Neo4j.
        node_id: Node ID to query.
        direction: "up" for parent methods, "down" for child overrides.

    Returns:
        List of dicts with keys: node_id, fqn, file, start_line.
    """
    query = OVERRIDES_UP if direction == "up" else OVERRIDES_DOWN
    key = "parent" if direction == "up" else "child"
    records = runner.execute(query, id=node_id)
    results = []
    for record in records:
        node = record[key]
        results.append({
            "node_id": node["node_id"],
            "fqn": node["fqn"],
            "file": node.get("file"),
            "start_line": node.get("start_line"),
        })
    return results
