"""Snapshot tests for kloc-intelligence context queries.

Parametrized over the 42 context cases from tests/cases.json.
Expected output is loaded from tests/snapshot-1802262244.json.

The context orchestrator (T12) wires all sub-modules (T01-T11) together
and execute_context_query() produces output matching kloc-cli exactly.
"""

import json
from pathlib import Path

import pytest

from tests.snapshot_compare import compare_snapshot, format_diff_report
from tests.conftest import requires_neo4j

# Paths relative to kloc monorepo root
KLOC_ROOT = Path(__file__).parent.parent.parent
CASES_PATH = KLOC_ROOT / "tests" / "cases.json"
SNAPSHOT_PATH = KLOC_ROOT / "tests" / "snapshot-1802262244.json"


def load_cases() -> list[dict]:
    """Load test cases from cases.json."""
    with open(CASES_PATH) as f:
        data = json.load(f)
    return data["cases"]


def load_snapshot() -> dict:
    """Load the golden snapshot baseline."""
    with open(SNAPSHOT_PATH) as f:
        return json.load(f)


# Load at module level for parametrize
_CASES = load_cases()
_SNAPSHOT = load_snapshot()
_CASE_IDS = [c["name"] for c in _CASES]


def execute_context_query(connection, symbol: str, depth: int, impl: bool) -> dict:
    """Execute a context query against Neo4j.

    Uses the context orchestrator to resolve the symbol, build the
    bidirectional context tree (used_by + uses + definition), and
    convert to contract-compliant dict output.

    Args:
        connection: Neo4jConnection from the loaded_database fixture.
        symbol: PHP symbol to resolve (e.g. 'App\\Entity\\Order').
        depth: BFS depth for expansion.
        impl: Whether to include polymorphic analysis.

    Returns:
        Dict matching the kloc-cli context JSON format.
    """
    from src.db.query_runner import QueryRunner
    from src.orchestration.context import execute_context
    from src.models.output import ContextOutput

    runner = QueryRunner(connection)
    result = execute_context(
        runner, symbol, depth=depth, limit=100, include_impl=impl
    )
    output = ContextOutput.from_result(result)
    return output.to_dict()


@pytest.mark.snapshot
class TestContextSnapshots:
    """Parametrized snapshot tests for the context command."""

    @pytest.mark.parametrize(
        "case",
        _CASES,
        ids=_CASE_IDS,
    )
    @requires_neo4j
    def test_context_query(self, loaded_database, case: dict):
        """Run a context query and compare against the golden snapshot."""
        case_name = case["name"]
        symbol = case["symbol"]
        depth = case["depth"]
        impl = case.get("impl", False)

        # Verify this case has expected output
        assert case_name in _SNAPSHOT, (
            f"Case '{case_name}' not found in snapshot baseline"
        )
        expected = _SNAPSHOT[case_name]

        # Execute the query
        actual = execute_context_query(loaded_database, symbol, depth, impl)

        # Compare
        result = compare_snapshot(case_name, expected, actual)
        if not result.passed:
            report = format_diff_report(result)
            pytest.fail(f"Snapshot mismatch:\n{report}")
