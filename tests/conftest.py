"""Shared test fixtures for kloc-intelligence."""

import os
from pathlib import Path

import pytest

from src.config import Neo4jConfig
from src.db.connection import Neo4jConnection, Neo4jConnectionError

# Monorepo layout assumes kloc-intelligence/ sits next to the other
# sub-projects (kloc-reference-project-php, etc.) under a single parent.
# `Path(__file__).parent.parent.parent` resolves to that parent.
_MONOREPO_ROOT = Path(__file__).parent.parent.parent

# Override paths via env vars when running outside the standard monorepo
# layout. When the underlying file is missing, the test that depends on
# it pytest.skips — no test bakes in `/Users/...` defaults.
REFERENCE_PROJECT_ROOT = Path(
    os.environ.get(
        "KLOC_REFERENCE_PROJECT_ROOT",
        str(_MONOREPO_ROOT / "kloc-reference-project-php"),
    )
)
REFERENCE_SYMFONY_KLOC = Path(
    os.environ.get(
        "KLOC_REFERENCE_SYMFONY_KLOC",
        str(REFERENCE_PROJECT_ROOT / ".kloc" / "symfony-kloc.json"),
    )
)


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


SOT_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent / "artifacts" / "kloc-dev" / "context-final" / "sot.json"
)


def _load_test_dataset(conn):
    """Load the context-final test dataset into Neo4j.

    Skips the calling test when the fixture sot.json is missing — that file
    lives in the parent monorepo's gitignored artifacts/ tree and isn't
    bundled with the standalone repo, so CI environments hit the skip.
    """
    from src.db.importer import import_edges, import_nodes, parse_sot
    from src.db.schema import drop_all, ensure_schema

    if not SOT_FIXTURE_PATH.is_file():
        pytest.skip(f"Test dataset sot.json not available at {SOT_FIXTURE_PATH}")

    drop_all(conn)
    ensure_schema(conn)
    nodes, edges = parse_sot(str(SOT_FIXTURE_PATH))
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
