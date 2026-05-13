"""Tests for schema management."""

from src.db.schema import (
    CONSTRAINTS,
    EDGE_TYPES,
    FLOW_EDGE_TYPES,
    INDEXES,
    NODE_KINDS,
    drop_all,
    ensure_schema,
    get_edge_count,
    get_node_count,
    verify_schema,
)

from .conftest import requires_neo4j


def test_node_kinds_count():
    """Test that there are exactly 13 node kinds."""
    assert len(NODE_KINDS) == 13


def test_edge_types_count():
    """Test that there are exactly 13 edge types."""
    assert len(EDGE_TYPES) == 13


def test_constraints_defined():
    """Test that constraints are defined."""
    assert "node_id_unique" in CONSTRAINTS


def test_v3_flow_constraints_defined():
    """v3 :Message/:Event/:HttpClient uniqueness constraints exist (AC #38)."""
    assert "message_id_unique" in CONSTRAINTS
    assert "event_id_unique" in CONSTRAINTS
    assert "http_client_id_unique" in CONSTRAINTS


def test_v3_flow_indexes_defined():
    """v3 indexes on Message.fqn, Event.fqn, HttpClient.service_id exist (AC #39)."""
    assert "message_fqn" in INDEXES
    assert "event_fqn" in INDEXES
    assert "http_client_service_id" in INDEXES


def test_flow_edge_types_documents_v3_set():
    """Flow edge types document the v3 set without FLOW_TRIGGERS."""
    assert "FLOW_TRIGGERS" not in FLOW_EDGE_TYPES
    for expected in (
        "FLOW_ENTRY",
        "FLOW_ENTRY_CLASS",
        "EMITS",
        "USES_HTTP_CLIENT",
        "HANDLED_BY",
        "OF_TYPE",
    ):
        assert expected in FLOW_EDGE_TYPES


def test_flow_id_and_flow_type_indexes_intact():
    """AC #39 — existing :Flow indexes survive the v3 schema additions."""
    assert "flow_id" in INDEXES
    assert "flow_type" in INDEXES


def test_indexes_defined():
    """Test that the required indexes are defined."""
    expected = [
        "node_fqn",
        "node_name",
        "node_kind",
        "node_symbol",
        "node_file",
        "class_fqn",
        "method_fqn",
        "interface_fqn",
        "value_kind",
        "call_kind",
        "node_explanation",
        "flow_id",
        "flow_type",
    ]
    for name in expected:
        assert name in INDEXES


@requires_neo4j
def test_ensure_schema(neo4j_connection):
    """Test that ensure_schema creates constraints and indexes."""
    result = ensure_schema(neo4j_connection)
    assert result["constraints"] >= 4
    assert result["indexes"] >= 10


@requires_neo4j
def test_ensure_schema_creates_v3_constraints(neo4j_connection):
    """SHOW CONSTRAINTS lists the v3 :Message/:Event/:HttpClient uniqueness rules."""
    ensure_schema(neo4j_connection)
    with neo4j_connection.session() as session:
        rows = list(session.run("SHOW CONSTRAINTS"))
    labels_seen = set()
    for r in rows:
        # Neo4j 5 surface: labelsOrTypes is a list of label strings
        labels = r.get("labelsOrTypes") or []
        for label in labels:
            labels_seen.add(label)
    assert "Message" in labels_seen
    assert "Event" in labels_seen
    assert "HttpClient" in labels_seen


@requires_neo4j
def test_ensure_schema_creates_v3_indexes(neo4j_connection):
    """SHOW INDEXES lists the v3 fqn/service_id indexes plus the existing Flow ones."""
    ensure_schema(neo4j_connection)
    with neo4j_connection.session() as session:
        rows = list(session.run("SHOW INDEXES"))
    by_name = {r["name"]: r for r in rows if r.get("name")}
    for expected in (
        "flow_id",
        "flow_type",
        "message_fqn",
        "event_fqn",
        "http_client_service_id",
    ):
        assert expected in by_name, f"missing index {expected!r}"


@requires_neo4j
def test_verify_schema(neo4j_connection):
    """Test that verify_schema returns counts."""
    ensure_schema(neo4j_connection)
    result = verify_schema(neo4j_connection)
    assert "constraints" in result
    assert "indexes" in result
    assert result["constraints"] >= 1
    assert result["indexes"] >= 10


@requires_neo4j
def test_ensure_schema_idempotent(neo4j_connection):
    """Test that calling ensure_schema twice is safe."""
    result1 = ensure_schema(neo4j_connection)
    result2 = ensure_schema(neo4j_connection)
    assert result1 == result2


@requires_neo4j
def test_drop_all(neo4j_connection):
    """Test that drop_all removes all nodes."""
    ensure_schema(neo4j_connection)
    drop_all(neo4j_connection)
    assert get_node_count(neo4j_connection) == 0
    assert get_edge_count(neo4j_connection) == 0


@requires_neo4j
def test_get_node_count(neo4j_connection):
    """Test get_node_count returns an integer."""
    drop_all(neo4j_connection)
    count = get_node_count(neo4j_connection)
    assert count == 0


@requires_neo4j
def test_get_edge_count(neo4j_connection):
    """Test get_edge_count returns an integer."""
    drop_all(neo4j_connection)
    count = get_edge_count(neo4j_connection)
    assert count == 0
