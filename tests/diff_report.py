#!/usr/bin/env python3
"""Diff report utility for kloc-intelligence snapshot validation.

Runs all 42 snapshot cases and compares kloc-intelligence output against
golden baseline from kloc-cli. Reports PASS/FAIL per case with field-level
diffs for failures.

Usage:
    cd kloc-intelligence
    uv run python tests/diff_report.py                    # full report
    uv run python tests/diff_report.py --case class-order-d1  # single case
    uv run python tests/diff_report.py --summary          # summary only

Requires Neo4j with test data loaded.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Add the kloc-intelligence root to the path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

KLOC_ROOT = PROJECT_ROOT.parent
CASES_PATH = KLOC_ROOT / "tests" / "cases.json"
SNAPSHOT_PATH = KLOC_ROOT / "tests" / "snapshot-1802262244.json"


def load_cases() -> list[dict]:
    """Load test cases from cases.json."""
    with open(CASES_PATH) as f:
        return json.load(f)["cases"]


def load_snapshot() -> dict:
    """Load the golden snapshot baseline."""
    with open(SNAPSHOT_PATH) as f:
        return json.load(f)


def execute_context_query(connection, symbol: str, depth: int, impl: bool) -> dict:
    """Execute a context query against Neo4j and return dict output."""
    from src.db.query_runner import QueryRunner
    from src.orchestration.context import execute_context
    from src.models.output import ContextOutput

    runner = QueryRunner(connection)
    result = execute_context(
        runner, symbol, depth=depth, limit=100, include_impl=impl
    )
    output = ContextOutput.from_result(result)
    return output.to_dict()


def compare_json(expected, actual, path: str = "$") -> list[dict]:
    """Recursively compare two JSON structures and return diffs."""
    diffs = []

    if type(expected) is not type(actual):
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if abs(float(expected) - float(actual)) > 1e-6:
                diffs.append({"path": path, "type": "value", "expected": expected, "actual": actual})
            return diffs
        diffs.append({"path": path, "type": "type", "expected": type(expected).__name__,
                       "actual": type(actual).__name__})
        return diffs

    if isinstance(expected, dict):
        all_keys = set(expected.keys()) | set(actual.keys())
        for key in sorted(all_keys):
            child = f"{path}.{key}"
            if key not in expected:
                diffs.append({"path": child, "type": "extra", "actual": repr(actual[key])[:60]})
            elif key not in actual:
                diffs.append({"path": child, "type": "missing", "expected": repr(expected[key])[:60]})
            else:
                diffs.extend(compare_json(expected[key], actual[key], child))
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            diffs.append({"path": f"{path}.__len__", "type": "value",
                          "expected": len(expected), "actual": len(actual)})
        for i in range(min(len(expected), len(actual))):
            diffs.extend(compare_json(expected[i], actual[i], f"{path}[{i}]"))
    elif isinstance(expected, float):
        if abs(expected - actual) > 1e-6:
            diffs.append({"path": path, "type": "value", "expected": expected, "actual": actual})
    elif expected != actual:
        diffs.append({"path": path, "type": "value", "expected": expected, "actual": actual})

    return diffs


def format_diff(diff: dict) -> str:
    """Format a single diff entry as a human-readable line."""
    if diff["type"] == "missing":
        return f"  MISSING {diff['path']}: expected {diff['expected']}"
    elif diff["type"] == "extra":
        return f"  EXTRA   {diff['path']}: got {diff['actual']}"
    elif diff["type"] == "type":
        return f"  TYPE    {diff['path']}: expected {diff['expected']}, got {diff['actual']}"
    else:
        return (f"  DIFF    {diff['path']}: "
                f"expected {repr(diff['expected'])[:40]}, got {repr(diff['actual'])[:40]}")


def run_report(cases: list[dict], snapshot: dict, connection,
               summary_only: bool = False) -> tuple[int, int]:
    """Run all cases and produce a diff report.

    Returns (pass_count, fail_count).
    """
    passed = 0
    failed = 0
    failures = []

    total = len(cases)
    start_time = time.time()

    for i, case in enumerate(cases, 1):
        name = case["name"]
        symbol = case["symbol"]
        depth = case["depth"]
        impl = case.get("impl", False)

        if name not in snapshot:
            print(f"  [{i:2d}/{total}] {name}: SKIP (no golden baseline)")
            continue

        expected = snapshot[name]

        try:
            actual = execute_context_query(connection, symbol, depth, impl)
            diffs = compare_json(expected, actual)

            if not diffs:
                passed += 1
                if not summary_only:
                    print(f"  [{i:2d}/{total}] {name}: PASS")
            else:
                failed += 1
                failures.append((name, diffs))
                if not summary_only:
                    print(f"  [{i:2d}/{total}] {name}: FAIL ({len(diffs)} diffs)")
                    for d in diffs[:5]:
                        print(f"    {format_diff(d)}")
                    if len(diffs) > 5:
                        print(f"    ... and {len(diffs) - 5} more diffs")
        except Exception as e:
            failed += 1
            failures.append((name, [{"path": "$", "type": "error", "expected": "", "actual": str(e)}]))
            if not summary_only:
                print(f"  [{i:2d}/{total}] {name}: ERROR ({e})")

    elapsed = time.time() - start_time

    # Summary
    print()
    print("=" * 60)
    print("DIFF REPORT SUMMARY")
    print("=" * 60)
    print(f"  Total cases: {total}")
    print(f"  Passed:      {passed}")
    print(f"  Failed:      {failed}")
    print(f"  Pass rate:   {passed}/{total} ({100 * passed / total:.0f}%)")
    print(f"  Time:        {elapsed:.1f}s")
    print()

    if failures:
        print("FAILED CASES:")
        for name, diffs in failures:
            diff_count = len(diffs)
            print(f"  {name}: {diff_count} diff(s)")
            for d in diffs[:3]:
                print(f"    {format_diff(d)}")
        print()

    return passed, failed


def main():
    parser = argparse.ArgumentParser(description="Snapshot diff report for kloc-intelligence")
    parser.add_argument("--case", help="Run a single case by name")
    parser.add_argument("--summary", action="store_true", help="Show summary only")
    parser.add_argument("--category", help="Filter by category (class, method, etc.)")
    args = parser.parse_args()

    # Load test data
    cases = load_cases()
    snapshot = load_snapshot()

    if args.case:
        cases = [c for c in cases if c["name"] == args.case]
        if not cases:
            print(f"Case '{args.case}' not found.")
            sys.exit(1)

    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
        if not cases:
            print(f"No cases with category '{args.category}'.")
            sys.exit(1)

    # Connect to Neo4j
    from src.config import Neo4jConfig
    from src.db.connection import Neo4jConnection

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)

    try:
        conn.verify_connectivity()
    except Exception as e:
        print(f"Cannot connect to Neo4j: {e}")
        print("Make sure Neo4j is running with the test dataset loaded.")
        sys.exit(1)

    print(f"Running diff report for {len(cases)} cases...")
    print(f"  Snapshot: {SNAPSHOT_PATH.name}")
    print()

    passed, failed = run_report(cases, snapshot, conn, summary_only=args.summary)

    conn.close()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
