"""Snapshot comparator for kloc-intelligence test output validation.

Recursively compares JSON structures and reports field-level diffs.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldDiff:
    path: str
    expected: Any
    actual: Any
    diff_type: str  # "missing", "extra", "value_mismatch", "type_mismatch"


@dataclass
class ComparisonResult:
    query_id: str
    passed: bool
    diffs: list[FieldDiff] = field(default_factory=list)

    def summary(self) -> str:
        if self.passed:
            return f"{self.query_id}: PASS"
        return f"{self.query_id}: FAIL ({len(self.diffs)} diffs)"


def compare_json(expected: Any, actual: Any, path: str = "$") -> list[FieldDiff]:
    """Recursively compare two JSON structures.

    - Objects: key ordering ignored, all keys must match
    - Arrays: order IS significant
    - Strings/ints: exact match
    - Floats: tolerance 1e-6
    """
    diffs = []
    if type(expected) is not type(actual):
        # Allow int/float cross-comparison
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if abs(float(expected) - float(actual)) > 1e-6:
                diffs.append(FieldDiff(path, expected, actual, "value_mismatch"))
            return diffs
        diffs.append(FieldDiff(path, expected, actual, "type_mismatch"))
        return diffs

    if isinstance(expected, dict):
        all_keys = set(expected.keys()) | set(actual.keys())
        for key in sorted(all_keys):
            child_path = f"{path}.{key}"
            if key not in expected:
                diffs.append(FieldDiff(child_path, None, actual[key], "extra"))
            elif key not in actual:
                diffs.append(FieldDiff(child_path, expected[key], None, "missing"))
            else:
                diffs.extend(compare_json(expected[key], actual[key], child_path))
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            diffs.append(
                FieldDiff(f"{path}.__len__", len(expected), len(actual), "value_mismatch")
            )
        for i in range(min(len(expected), len(actual))):
            diffs.extend(compare_json(expected[i], actual[i], f"{path}[{i}]"))
    elif isinstance(expected, float):
        if abs(expected - actual) > 1e-6:
            diffs.append(FieldDiff(path, expected, actual, "value_mismatch"))
    elif expected != actual:
        diffs.append(FieldDiff(path, expected, actual, "value_mismatch"))

    return diffs


def compare_snapshot(query_id: str, expected: dict, actual: dict) -> ComparisonResult:
    """Compare expected and actual JSON output for a single query case."""
    diffs = compare_json(expected, actual)
    return ComparisonResult(query_id=query_id, passed=len(diffs) == 0, diffs=diffs)


def format_diff_report(result: ComparisonResult, max_diffs: int = 20) -> str:
    """Format a ComparisonResult into a human-readable report."""
    lines = [result.summary()]
    if not result.passed:
        for diff in result.diffs[:max_diffs]:
            if diff.diff_type == "missing":
                lines.append(f"  MISSING {diff.path}: expected {repr(diff.expected)[:80]}")
            elif diff.diff_type == "extra":
                lines.append(f"  EXTRA   {diff.path}: got {repr(diff.actual)[:80]}")
            elif diff.diff_type == "value_mismatch":
                lines.append(
                    f"  DIFF    {diff.path}: "
                    f"expected {repr(diff.expected)[:40]}, got {repr(diff.actual)[:40]}"
                )
            elif diff.diff_type == "type_mismatch":
                lines.append(
                    f"  TYPE    {diff.path}: "
                    f"expected {type(diff.expected).__name__}, "
                    f"got {type(diff.actual).__name__}"
                )
        if len(result.diffs) > max_diffs:
            lines.append(f"  ... and {len(result.diffs) - max_diffs} more diffs")
    return "\n".join(lines)
