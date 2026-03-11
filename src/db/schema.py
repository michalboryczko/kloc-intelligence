"""Schema management for kloc-intelligence Neo4j database."""

from .connection import Neo4jConnection

NODE_KINDS = [
    "Class",
    "Interface",
    "Trait",
    "Enum",
    "Method",
    "Function",
    "Property",
    "Const",
    "EnumCase",
    "Argument",
    "Value",
    "Call",
    "File",
]

EDGE_TYPES = [
    "contains",
    "uses",
    "extends",
    "implements",
    "overrides",
    "type_hint",
    "calls",
    "receiver",
    "argument",
    "produces",
    "assigned_from",
    "type_of",
    "return_type",
]

CONSTRAINTS = {
    "node_id_unique": (
        "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.node_id IS UNIQUE"
    ),
}

INDEXES = {
    "node_fqn": "CREATE INDEX node_fqn IF NOT EXISTS FOR (n:Node) ON (n.fqn)",
    "node_name": "CREATE INDEX node_name IF NOT EXISTS FOR (n:Node) ON (n.name)",
    "node_kind": "CREATE INDEX node_kind IF NOT EXISTS FOR (n:Node) ON (n.kind)",
    "node_symbol": "CREATE INDEX node_symbol IF NOT EXISTS FOR (n:Node) ON (n.symbol)",
    "node_file": "CREATE INDEX node_file IF NOT EXISTS FOR (n:Node) ON (n.file)",
    "class_fqn": "CREATE INDEX class_fqn IF NOT EXISTS FOR (n:Class) ON (n.fqn)",
    "method_fqn": "CREATE INDEX method_fqn IF NOT EXISTS FOR (n:Method) ON (n.fqn)",
    "interface_fqn": "CREATE INDEX interface_fqn IF NOT EXISTS FOR (n:Interface) ON (n.fqn)",
    "value_kind": "CREATE INDEX value_kind IF NOT EXISTS FOR (n:Value) ON (n.kind)",
    "call_kind": "CREATE INDEX call_kind IF NOT EXISTS FOR (n:Call) ON (n.kind)",
}


def ensure_schema(connection: Neo4jConnection) -> dict:
    """Create all constraints and indexes, returns verify result."""
    with connection.session() as session:
        for cypher in CONSTRAINTS.values():
            session.run(cypher)
        for cypher in INDEXES.values():
            session.run(cypher)
    return verify_schema(connection)


def verify_schema(connection: Neo4jConnection) -> dict:
    """Return constraint and index counts."""
    with connection.session() as session:
        constraints_result = session.run("SHOW CONSTRAINTS")
        constraints = list(constraints_result)
        indexes_result = session.run("SHOW INDEXES")
        indexes = [r for r in indexes_result if r["type"] != "LOOKUP"]
    return {
        "constraints": len(constraints),
        "indexes": len(indexes),
    }


def drop_all(connection: Neo4jConnection) -> None:
    """Delete all nodes and relationships."""
    with connection.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


def get_node_count(connection: Neo4jConnection) -> int:
    """Return total node count."""
    with connection.session() as session:
        result = session.run("MATCH (n) RETURN count(n) AS count")
        return result.single()["count"]


def get_edge_count(connection: Neo4jConnection) -> int:
    """Return total relationship count."""
    with connection.session() as session:
        result = session.run("MATCH ()-[r]->() RETURN count(r) AS count")
        return result.single()["count"]
