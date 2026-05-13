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

# v3 :Flow-graph edge types (documentation-only; FLOW_TRIGGERS retired in v3).
FLOW_EDGE_TYPES = [
    "FLOW_ENTRY",
    "FLOW_ENTRY_CLASS",
    "EMITS",
    "USES_HTTP_CLIENT",
    "HANDLED_BY",
    "OF_TYPE",
]

CONSTRAINTS = {
    "node_id_unique": (
        "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.node_id IS UNIQUE"
    ),
    "message_id_unique": (
        "CREATE CONSTRAINT message_id_unique IF NOT EXISTS FOR (n:Message) REQUIRE n.id IS UNIQUE"
    ),
    "event_id_unique": (
        "CREATE CONSTRAINT event_id_unique IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE"
    ),
    "http_client_id_unique": (
        "CREATE CONSTRAINT http_client_id_unique IF NOT EXISTS "
        "FOR (n:HttpClient) REQUIRE n.id IS UNIQUE"
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
    "node_explanation": "CREATE INDEX node_explanation IF NOT EXISTS FOR (n:Node) ON (n.explanation)",
    "flow_id": "CREATE INDEX flow_id IF NOT EXISTS FOR (n:Flow) ON (n.flow_id)",
    "flow_type": "CREATE INDEX flow_type IF NOT EXISTS FOR (n:Flow) ON (n.type)",
    "message_fqn": "CREATE INDEX message_fqn IF NOT EXISTS FOR (n:Message) ON (n.fqn)",
    "event_fqn": "CREATE INDEX event_fqn IF NOT EXISTS FOR (n:Event) ON (n.fqn)",
    "http_client_service_id": (
        "CREATE INDEX http_client_service_id IF NOT EXISTS FOR (n:HttpClient) ON (n.service_id)"
    ),
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
