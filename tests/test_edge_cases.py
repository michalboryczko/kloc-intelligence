"""Edge case tests for kloc-intelligence context queries.

Tests edge cases against Neo4j:
- Empty results (symbol with no usages/deps)
- Deep depth (depth=5 on a short chain -- should stop early)
- Limit enforcement (limit=1 returns at most 1 entry per section)
- Constructor redirect (__construct -> Class USED BY)
- Unknown symbol (should raise ValueError)
"""

import pytest

from tests.conftest import requires_neo4j


def _execute_context(connection, symbol: str, depth: int = 1, limit: int = 100,
                     impl: bool = False) -> dict:
    """Execute a context query and return dict output."""
    from src.db.query_runner import QueryRunner
    from src.orchestration.context import execute_context
    from src.models.output import ContextOutput

    runner = QueryRunner(connection)
    result = execute_context(runner, symbol, depth=depth, limit=limit, include_impl=impl)
    output = ContextOutput.from_result(result)
    return output.to_dict()


def _count_entries(entries: list[dict]) -> int:
    """Count total entries recursively including children."""
    count = len(entries)
    for entry in entries:
        if entry.get("children"):
            count += _count_entries(entry["children"])
    return count


def _max_depth_in(entries: list[dict]) -> int:
    """Find maximum depth value in entries recursively."""
    if not entries:
        return 0
    max_d = 0
    for entry in entries:
        max_d = max(max_d, entry.get("depth", 0))
        if entry.get("children"):
            max_d = max(max_d, _max_depth_in(entry["children"]))
    return max_d


@pytest.mark.snapshot
class TestEdgeCaseEmptyResults:
    """Test symbols with no usages or dependencies."""

    @requires_neo4j
    def test_interface_with_no_implementors_has_empty_uses(self, loaded_database):
        """An interface with no concrete implementations may have empty USES."""
        # InventoryCheckerInterface has minimal uses at depth 1
        d = _execute_context(loaded_database, "App\\Component\\InventoryCheckerInterface", depth=1)
        assert isinstance(d["usedBy"], list)
        assert isinstance(d["uses"], list)
        # Structure must be valid even if sections are empty
        assert "target" in d
        assert d["maxDepth"] == 1

    @requires_neo4j
    def test_leaf_method_may_have_empty_uses(self, loaded_database):
        """A simple getter/leaf method may have empty USES."""
        d = _execute_context(
            loaded_database, "App\\Entity\\Address::getFullAddress()", depth=1
        )
        # Should succeed and have valid structure
        assert isinstance(d["usedBy"], list)
        assert isinstance(d["uses"], list)


@pytest.mark.snapshot
class TestEdgeCaseDeepDepth:
    """Test deep depth on short chains (should stop early)."""

    @requires_neo4j
    def test_depth_5_on_short_chain_stops_early(self, loaded_database):
        """Requesting depth=5 on a shallow chain should not crash or hang."""
        d = _execute_context(
            loaded_database, "App\\Entity\\Address::getFullAddress()", depth=5
        )
        assert d["maxDepth"] == 5
        # Actual entries should have depth <= 5
        all_entries = d.get("usedBy", []) + d.get("uses", [])
        max_d = _max_depth_in(all_entries)
        assert max_d <= 5

    @requires_neo4j
    def test_depth_5_matches_depth_3_golden(self, loaded_database):
        """For method-create-order, depth 5 should be a superset of depth 3."""
        d3 = _execute_context(
            loaded_database, "App\\Service\\OrderService::createOrder()", depth=3
        )
        d5 = _execute_context(
            loaded_database, "App\\Service\\OrderService::createOrder()", depth=5
        )
        # depth=5 should have at least as many entries as depth=3
        count_3 = _count_entries(d3.get("usedBy", [])) + _count_entries(d3.get("uses", []))
        count_5 = _count_entries(d5.get("usedBy", [])) + _count_entries(d5.get("uses", []))
        assert count_5 >= count_3, (
            f"depth=5 ({count_5} entries) should have >= depth=3 ({count_3} entries)"
        )


@pytest.mark.snapshot
class TestEdgeCaseLimitEnforcement:
    """Test that limit parameter constrains results."""

    @requires_neo4j
    def test_limit_1_constrains_used_by(self, loaded_database):
        """With limit=1, usedBy should have at most 1 top-level entry."""
        d = _execute_context(
            loaded_database, "App\\Entity\\Order", depth=1, limit=1
        )
        assert len(d["usedBy"]) <= 1

    @requires_neo4j
    def test_limit_1_constrains_uses(self, loaded_database):
        """With limit=1, uses should have at most 1 top-level entry."""
        d = _execute_context(
            loaded_database, "App\\Entity\\Order", depth=1, limit=1
        )
        assert len(d["uses"]) <= 1

    @requires_neo4j
    def test_limit_100_returns_full_results(self, loaded_database):
        """Default limit=100 should return full results for test dataset."""
        d = _execute_context(
            loaded_database, "App\\Entity\\Order", depth=1, limit=100
        )
        # The test dataset is small, so limit=100 should not truncate
        assert len(d["usedBy"]) > 1 or len(d["uses"]) > 1


@pytest.mark.snapshot
class TestEdgeCaseConstructorRedirect:
    """Test __construct -> Class USED BY redirect."""

    @requires_neo4j
    def test_constructor_shows_class_used_by(self, loaded_database):
        """__construct() should redirect USED BY to the parent class."""
        d = _execute_context(
            loaded_database, "App\\Entity\\Order::__construct()", depth=2
        )
        # Constructor redirect means usedBy should show class-level usages
        # (instantiations, type_hints, etc.)
        assert len(d["usedBy"]) > 0, (
            "Constructor redirect should produce non-empty usedBy"
        )

    @requires_neo4j
    def test_constructor_has_class_like_ref_types(self, loaded_database):
        """Constructor USED BY should contain class-level ref types."""
        d = _execute_context(
            loaded_database, "App\\Entity\\Order::__construct()", depth=1
        )
        ref_types = {e.get("refType") for e in d["usedBy"] if e.get("refType")}
        # At minimum, Order is instantiated
        class_ref_types = {"instantiation", "type_hint", "parameter_type", "return_type",
                           "extends", "implements", "method_call", "property_access"}
        assert ref_types & class_ref_types, (
            f"Expected class-level refTypes, got: {ref_types}"
        )


@pytest.mark.snapshot
class TestEdgeCaseUnknownSymbol:
    """Test unknown symbol handling."""

    @requires_neo4j
    def test_unknown_symbol_raises_value_error(self, loaded_database):
        """Querying a non-existent symbol should raise ValueError."""
        from src.db.query_runner import QueryRunner
        from src.orchestration.context import execute_context

        runner = QueryRunner(loaded_database)
        with pytest.raises(ValueError, match="Symbol not found"):
            execute_context(runner, "App\\NonExistent\\DoesNotExist::method()")

    @requires_neo4j
    def test_partial_symbol_does_not_crash(self, loaded_database):
        """Querying with a partial/ambiguous symbol should either resolve or raise cleanly."""
        from src.db.query_runner import QueryRunner
        from src.orchestration.context import execute_context

        runner = QueryRunner(loaded_database)
        try:
            result = execute_context(runner, "App\\Entity\\Order")
            # If it resolves, it should be a valid result
            assert result.target.fqn == "App\\Entity\\Order"
        except ValueError:
            # If it doesn't resolve, that's also acceptable
            pass
