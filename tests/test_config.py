"""Tests for Neo4jConfig."""

from src.config import Neo4jConfig


def test_config_defaults():
    """Test that Neo4jConfig has correct defaults."""
    config = Neo4jConfig()
    assert config.uri == "bolt://localhost:7687"
    assert config.username == "neo4j"
    assert config.password == "kloc-intelligence"
    assert config.database == "neo4j"
    assert config.max_connection_pool_size == 50
    assert config.connection_acquisition_timeout == 60.0


def test_config_from_env(monkeypatch):
    """Test that from_env reads environment variables."""
    monkeypatch.setenv("NEO4J_URI", "bolt://custom:7688")
    monkeypatch.setenv("NEO4J_USERNAME", "admin")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("NEO4J_DATABASE", "testdb")

    config = Neo4jConfig.from_env()
    assert config.uri == "bolt://custom:7688"
    assert config.username == "admin"
    assert config.password == "secret"
    assert config.database == "testdb"


def test_config_from_env_defaults(monkeypatch):
    """Test that from_env uses defaults when env vars are not set."""
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_USERNAME", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.delenv("NEO4J_DATABASE", raising=False)

    config = Neo4jConfig.from_env()
    assert config.uri == "bolt://localhost:7687"
    assert config.username == "neo4j"
    assert config.password == "kloc-intelligence"
    assert config.database == "neo4j"
