"""Tests for schema management."""

from src.db.schema import (
    NODE_KINDS,
    EDGE_TYPES,
    CONSTRAINTS,
    INDEXES,
    ensure_schema,
    verify_schema,
    drop_all,
    get_node_count,
    get_edge_count,
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


def test_indexes_defined():
    """Test that all 10 indexes are defined."""
    assert len(INDEXES) == 10
    expected = [
        "node_fqn", "node_name", "node_kind", "node_symbol", "node_file",
        "class_fqn", "method_fqn", "interface_fqn",
        "value_kind", "call_kind",
    ]
    for name in expected:
        assert name in INDEXES


@requires_neo4j
def test_ensure_schema(neo4j_connection):
    """Test that ensure_schema creates constraints and indexes."""
    result = ensure_schema(neo4j_connection)
    assert result["constraints"] >= 1
    assert result["indexes"] >= 10


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
