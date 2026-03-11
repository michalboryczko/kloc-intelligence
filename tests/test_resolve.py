"""Tests for symbol resolution queries."""

from src.db.query_runner import QueryRunner
from src.db.queries.resolve import resolve_symbol, SEARCHABLE_KINDS
from src.models.node import NodeData
from .conftest import requires_neo4j


@requires_neo4j
class TestResolveSymbol:
    """Integration tests for resolve_symbol against loaded Neo4j database."""

    def test_exact_fqn_class(self, loaded_database):
        """Test exact FQN match for a Class."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "App\\Entity\\Order")
        assert len(results) >= 1
        assert any(n.kind == "Class" and n.fqn == "App\\Entity\\Order" for n in results)

    def test_exact_fqn_method(self, loaded_database):
        """Test exact FQN match for a Method."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "App\\Service\\NotificationService::notifyOrderCreated()")
        assert len(results) >= 1
        assert results[0].kind == "Method"
        assert results[0].fqn == "App\\Service\\NotificationService::notifyOrderCreated()"

    def test_exact_fqn_value_node(self, loaded_database):
        """Test that Value nodes are findable by exact FQN match."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(
            runner,
            "App\\Service\\VersionedOrderService::getOrdersByVersion().$version",
        )
        assert len(results) >= 1
        # Value node should be in results
        assert any(n.kind == "Value" for n in results)

    def test_value_argument_dedup(self, loaded_database):
        """Test that when both Value and Argument exist, Argument is deduped."""
        runner = QueryRunner(loaded_database)
        # This FQN has both Value and Argument nodes
        results = resolve_symbol(
            runner,
            "App\\Service\\VersionedOrderService::getOrdersByVersion().$version",
        )
        kinds = [n.kind for n in results]
        assert "Value" in kinds
        assert "Argument" not in kinds

    def test_case_insensitive_fallback(self, loaded_database):
        """Test case-insensitive matching as fallback."""
        runner = QueryRunner(loaded_database)
        # Use wrong case - should still find via case-insensitive
        results = resolve_symbol(runner, "app\\entity\\order")
        assert len(results) >= 1
        assert any(n.fqn == "App\\Entity\\Order" for n in results)

    def test_suffix_match(self, loaded_database):
        """Test suffix matching for short class names."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "Order")
        assert len(results) >= 1
        # Should find App\Entity\Order among results
        fqns = [n.fqn for n in results]
        assert any("Order" in fqn for fqn in fqns)

    def test_short_name_match(self, loaded_database):
        """Test short name match for method names."""
        runner = QueryRunner(loaded_database)
        # Use a method name with :: prefix
        results = resolve_symbol(runner, "SomeClass::notifyOrderCreated()")
        # Should fall through to short name match
        assert len(results) >= 1
        assert any(n.name == "notifyOrderCreated" for n in results)

    def test_no_match(self, loaded_database):
        """Test that non-existent symbol returns empty list."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "Totally\\NonExistent\\Symbol\\That\\Does\\Not\\Exist")
        assert results == []

    def test_leading_backslash_stripped(self, loaded_database):
        """Test that leading backslash is stripped from query."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "\\App\\Entity\\Order")
        assert len(results) >= 1
        assert any(n.fqn == "App\\Entity\\Order" for n in results)

    def test_whitespace_stripped(self, loaded_database):
        """Test that whitespace is stripped from query."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "  App\\Entity\\Order  ")
        assert len(results) >= 1
        assert any(n.fqn == "App\\Entity\\Order" for n in results)

    def test_result_types(self, loaded_database):
        """Test that results are NodeData instances."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "App\\Entity\\Order")
        assert all(isinstance(n, NodeData) for n in results)

    def test_interface_resolve(self, loaded_database):
        """Test resolving an interface."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "App\\Repository\\CustomerRepositoryInterface")
        assert len(results) >= 1
        assert results[0].kind == "Interface"

    def test_property_resolve(self, loaded_database):
        """Test resolving a property."""
        runner = QueryRunner(loaded_database)
        results = resolve_symbol(runner, "App\\Dto\\OrderOutput::$customerEmail")
        assert len(results) >= 1
        assert results[0].kind == "Property"

    def test_contains_fallback(self, loaded_database):
        """Test contains fallback for partial matches."""
        runner = QueryRunner(loaded_database)
        # A substring that doesn't match any suffix or name but does occur in FQNs
        results = resolve_symbol(runner, "EmailSender")
        assert len(results) >= 1
        assert any("EmailSender" in n.fqn for n in results)

    def test_searchable_kinds_list(self):
        """Test that SEARCHABLE_KINDS contains expected kinds."""
        assert "Class" in SEARCHABLE_KINDS
        assert "Interface" in SEARCHABLE_KINDS
        assert "Method" in SEARCHABLE_KINDS
        assert "Property" in SEARCHABLE_KINDS
        assert "File" in SEARCHABLE_KINDS
        # Internal kinds should NOT be in searchable
        assert "Value" not in SEARCHABLE_KINDS
        assert "Argument" not in SEARCHABLE_KINDS
        assert "Call" not in SEARCHABLE_KINDS

    def test_name_without_parens(self, loaded_database):
        """Test that method name with parens falls back to name without parens."""
        runner = QueryRunner(loaded_database)
        # Use a method name that won't match suffix but will match short name
        results = resolve_symbol(runner, "FakeClass::notifyOrderCreated()")
        assert len(results) >= 1
        # Should have found via short name "notifyOrderCreated" (without parens)
        assert any(n.name == "notifyOrderCreated" for n in results)
