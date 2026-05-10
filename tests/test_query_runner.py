"""Tests for QueryRunner."""

from src.db.query_runner import QueryRunner

from .conftest import requires_neo4j


@requires_neo4j
class TestQueryRunner:
    """Integration tests for QueryRunner against Neo4j."""

    def test_execute_returns_list(self, loaded_database):
        """Test that execute returns a list of records."""
        runner = QueryRunner(loaded_database)
        records = runner.execute("MATCH (n:Node) RETURN n LIMIT 5")
        assert isinstance(records, list)
        assert len(records) == 5

    def test_execute_with_params(self, loaded_database):
        """Test execute with query parameters."""
        runner = QueryRunner(loaded_database)
        records = runner.execute(
            "MATCH (n:Node {kind: $kind}) RETURN n LIMIT 3",
            kind="Class",
        )
        assert len(records) == 3
        for r in records:
            assert r["n"]["kind"] == "Class"

    def test_execute_empty_result(self, loaded_database):
        """Test execute returns empty list when no matches."""
        runner = QueryRunner(loaded_database)
        records = runner.execute(
            "MATCH (n:Node {fqn: $fqn}) RETURN n",
            fqn="NonExistent\\Class\\That\\Does\\Not\\Exist",
        )
        assert records == []

    def test_execute_single_returns_record(self, loaded_database):
        """Test execute_single returns a single record."""
        runner = QueryRunner(loaded_database)
        record = runner.execute_single(
            "MATCH (n:Node {fqn: $fqn}) RETURN n",
            fqn="App\\Entity\\Order",
        )
        assert record is not None
        assert record["n"]["fqn"] == "App\\Entity\\Order"

    def test_execute_single_returns_none(self, loaded_database):
        """Test execute_single returns None when no match."""
        runner = QueryRunner(loaded_database)
        record = runner.execute_single(
            "MATCH (n:Node {fqn: $fqn}) RETURN n",
            fqn="NonExistent\\Class",
        )
        assert record is None

    def test_execute_value_returns_scalar(self, loaded_database):
        """Test execute_value returns a scalar value."""
        runner = QueryRunner(loaded_database)
        count = runner.execute_value("MATCH (n:Node) RETURN count(n)")
        assert isinstance(count, int)
        assert count == 1154

    def test_execute_value_returns_none(self, loaded_database):
        """Test execute_value returns None when no match."""
        runner = QueryRunner(loaded_database)
        value = runner.execute_value("MATCH (n:Node {fqn: 'nonexistent'}) RETURN n.name")
        assert value is None

    def test_execute_count(self, loaded_database):
        """Test execute_count returns an integer count."""
        runner = QueryRunner(loaded_database)
        count = runner.execute_count("MATCH (n:Node) RETURN count(n)")
        assert count == 1154

    def test_execute_count_zero(self, loaded_database):
        """Test execute_count returns 0 for empty result."""
        runner = QueryRunner(loaded_database)
        count = runner.execute_count("MATCH (n:Node {fqn: 'nonexistent'}) RETURN count(n)")
        assert count == 0
