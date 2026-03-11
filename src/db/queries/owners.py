"""Cypher queries and execution functions for owners (containment chain)."""

from ..query_runner import QueryRunner
from ..result_mapper import record_to_node
from ...models.node import NodeData

# Fetch a single node by ID
FETCH_NODE = """
MATCH (n:Node {node_id: $id})
RETURN n
"""

# Find the parent that CONTAINS a node
FETCH_PARENT = """
MATCH (n:Node {node_id: $id})<-[:CONTAINS]-(parent:Node)
RETURN parent.node_id AS pid
"""


def fetch_node(runner: QueryRunner, node_id: str) -> NodeData | None:
    """Fetch a single node by ID."""
    record = runner.execute_single(FETCH_NODE, id=node_id)
    if record is None:
        return None
    return record_to_node(record)


def get_owners_chain(runner: QueryRunner, node_id: str) -> list[NodeData]:
    """Walk the containment chain upward from node_id to File root.

    Returns a list starting with the target node, then its parent, grandparent,
    etc. up to the File root. Matches kloc-cli behavior exactly.
    """
    chain: list[NodeData] = []
    current_id: str | None = node_id

    while current_id:
        record = runner.execute_single(FETCH_NODE, id=current_id)
        if record is None:
            break
        chain.append(record_to_node(record))

        parent = runner.execute_single(FETCH_PARENT, id=current_id)
        current_id = parent["pid"] if parent else None

    return chain
