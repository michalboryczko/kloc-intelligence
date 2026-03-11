"""Shared test fixtures for kloc-intelligence."""

from pathlib import Path

import pytest

from src.config import Neo4jConfig
from src.db.connection import Neo4jConnection, Neo4jConnectionError


def neo4j_is_available() -> bool:
    """Check if Neo4j is available for integration tests."""
    config = Neo4jConfig.from_env()
    try:
        conn = Neo4jConnection(config)
        conn.verify_connectivity()
        conn.close()
        return True
    except (Neo4jConnectionError, Exception):
        return False


NEO4J_AVAILABLE = neo4j_is_available()

requires_neo4j = pytest.mark.skipif(
    not NEO4J_AVAILABLE,
    reason="Neo4j is not available",
)


@pytest.fixture
def neo4j_config() -> Neo4jConfig:
    """Return a Neo4jConfig for testing."""
    return Neo4jConfig.from_env()


@pytest.fixture
def neo4j_connection(neo4j_config: Neo4jConfig):
    """Provide a Neo4jConnection for integration tests."""
    conn = Neo4jConnection(neo4j_config)
    yield conn
    conn.close()


def _load_test_dataset(conn):
    """Load the context-final test dataset into Neo4j."""
    from src.db.schema import ensure_schema, drop_all
    from src.db.importer import parse_sot, import_nodes, import_edges

    sot_path = (
        Path(__file__).parent.parent.parent
        / "artifacts"
        / "kloc-dev"
        / "context-final"
        / "sot.json"
    )
    assert sot_path.exists(), f"Test dataset not found at {sot_path}"

    drop_all(conn)
    ensure_schema(conn)
    nodes, edges = parse_sot(str(sot_path))
    import_nodes(conn, nodes)
    import_edges(conn, edges)
    return len(nodes)


def _db_has_data(conn, expected_min: int = 1000) -> bool:
    """Check if the database still has data (not cleared by another test)."""
    from src.db.schema import get_node_count
    return get_node_count(conn) >= expected_min


@pytest.fixture(scope="session")
def _loaded_database_conn():
    """Session-scoped connection for loaded_database."""
    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    try:
        conn.verify_connectivity()
    except Neo4jConnectionError:
        pytest.skip("Neo4j is not available")

    _load_test_dataset(conn)
    yield conn
    conn.close()


@pytest.fixture
def loaded_database(_loaded_database_conn):
    """Provide a Neo4j connection with test data loaded.

    Reloads data if another test (e.g. test_import) cleared the database.
    """
    if not _db_has_data(_loaded_database_conn):
        _load_test_dataset(_loaded_database_conn)
    return _loaded_database_conn
