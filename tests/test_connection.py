"""Tests for Neo4jConnection."""

import pytest
from src.config import Neo4jConfig
from src.db.connection import Neo4jConnection, Neo4jConnectionError
from .conftest import requires_neo4j


def test_connection_creation():
    """Test that connection can be created with a config."""
    config = Neo4jConfig(uri="bolt://localhost:7687")
    conn = Neo4jConnection(config)
    assert conn is not None
    conn.close()


def test_connection_context_manager():
    """Test that connection works as a context manager."""
    config = Neo4jConfig(uri="bolt://localhost:7687")
    with Neo4jConnection(config) as conn:
        assert conn is not None


def test_connection_invalid_uri():
    """Test that invalid driver config raises Neo4jConnectionError."""
    config = Neo4jConfig(uri="bolt://invalid-host-that-does-not-exist:9999")
    # Driver creation itself doesn't fail; verify_connectivity does
    conn = Neo4jConnection(config)
    with pytest.raises(Neo4jConnectionError):
        conn.verify_connectivity()
    conn.close()


@requires_neo4j
def test_verify_connectivity(neo4j_connection):
    """Test that verify_connectivity succeeds with running Neo4j."""
    neo4j_connection.verify_connectivity()


@requires_neo4j
def test_session(neo4j_connection):
    """Test that we can open a session and run a query."""
    with neo4j_connection.session() as session:
        result = session.run("RETURN 1 AS n")
        record = result.single()
        assert record["n"] == 1
