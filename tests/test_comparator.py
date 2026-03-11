"""Unit tests for the snapshot comparator."""

from tests.snapshot_compare import (
    FieldDiff,
    ComparisonResult,
    compare_json,
    compare_snapshot,
    format_diff_report,
)


class TestCompareJson:
    """Test the compare_json recursive comparator."""

    def test_identical_dicts_no_diffs(self):
        a = {"name": "Order", "kind": "Class", "line": 9}
        b = {"name": "Order", "kind": "Class", "line": 9}
        diffs = compare_json(a, b)
        assert diffs == []

    def test_identical_nested_dicts_no_diffs(self):
        a = {"target": {"fqn": "App\\Entity\\Order", "file": "src/Order.php"}, "depth": 1}
        b = {"target": {"fqn": "App\\Entity\\Order", "file": "src/Order.php"}, "depth": 1}
        diffs = compare_json(a, b)
        assert diffs == []

    def test_identical_lists_no_diffs(self):
        a = [1, 2, 3]
        b = [1, 2, 3]
        diffs = compare_json(a, b)
        assert diffs == []

    def test_missing_key_detected(self):
        a = {"name": "Order", "kind": "Class"}
        b = {"name": "Order"}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].diff_type == "missing"
        assert diffs[0].path == "$.kind"
        assert diffs[0].expected == "Class"
        assert diffs[0].actual is None

    def test_extra_key_detected(self):
        a = {"name": "Order"}
        b = {"name": "Order", "extra": True}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].diff_type == "extra"
        assert diffs[0].path == "$.extra"
        assert diffs[0].actual is True

    def test_value_mismatch_string(self):
        a = {"name": "Order"}
        b = {"name": "Customer"}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].diff_type == "value_mismatch"
        assert diffs[0].expected == "Order"
        assert diffs[0].actual == "Customer"

    def test_value_mismatch_int(self):
        a = {"line": 9}
        b = {"line": 42}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].diff_type == "value_mismatch"

    def test_type_mismatch_detected(self):
        a = {"val": "string"}
        b = {"val": [1, 2]}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].diff_type == "type_mismatch"
        assert diffs[0].path == "$.val"

    def test_nested_diffs(self):
        a = {"target": {"fqn": "App\\Order", "line": 9}}
        b = {"target": {"fqn": "App\\Customer", "line": 9}}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].path == "$.target.fqn"
        assert diffs[0].diff_type == "value_mismatch"

    def test_deeply_nested_diffs(self):
        a = {"a": {"b": {"c": {"d": 1}}}}
        b = {"a": {"b": {"c": {"d": 2}}}}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].path == "$.a.b.c.d"

    def test_list_length_mismatch(self):
        a = [1, 2, 3]
        b = [1, 2]
        diffs = compare_json(a, b)
        assert any(d.path == "$.__len__" for d in diffs)
        len_diff = [d for d in diffs if d.path == "$.__len__"][0]
        assert len_diff.expected == 3
        assert len_diff.actual == 2

    def test_list_item_mismatch(self):
        a = [1, 2, 3]
        b = [1, 99, 3]
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].path == "$[1]"
        assert diffs[0].expected == 2
        assert diffs[0].actual == 99

    def test_list_of_dicts_mismatch(self):
        a = [{"fqn": "A"}, {"fqn": "B"}]
        b = [{"fqn": "A"}, {"fqn": "C"}]
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].path == "$[1].fqn"

    def test_float_tolerance_pass(self):
        a = {"score": 1.0000001}
        b = {"score": 1.0000002}
        diffs = compare_json(a, b)
        assert diffs == []

    def test_float_tolerance_fail(self):
        a = {"score": 1.0}
        b = {"score": 2.0}
        diffs = compare_json(a, b)
        assert len(diffs) == 1

    def test_int_float_crosstype_pass(self):
        a = {"val": 1}
        b = {"val": 1.0}
        diffs = compare_json(a, b)
        assert diffs == []

    def test_int_float_crosstype_fail(self):
        a = {"val": 1}
        b = {"val": 2.5}
        diffs = compare_json(a, b)
        assert len(diffs) == 1

    def test_none_values(self):
        a = {"val": None}
        b = {"val": None}
        diffs = compare_json(a, b)
        assert diffs == []

    def test_none_vs_value_type_mismatch(self):
        a = {"val": None}
        b = {"val": "something"}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].diff_type == "type_mismatch"

    def test_bool_values(self):
        a = {"flag": True}
        b = {"flag": False}
        diffs = compare_json(a, b)
        assert len(diffs) == 1
        assert diffs[0].diff_type == "value_mismatch"

    def test_empty_dict_matches(self):
        diffs = compare_json({}, {})
        assert diffs == []

    def test_empty_list_matches(self):
        diffs = compare_json([], [])
        assert diffs == []

    def test_key_order_ignored(self):
        a = {"z": 1, "a": 2, "m": 3}
        b = {"a": 2, "m": 3, "z": 1}
        diffs = compare_json(a, b)
        assert diffs == []

    def test_multiple_diffs_reported(self):
        a = {"x": 1, "y": 2, "z": 3}
        b = {"x": 10, "y": 20, "z": 30}
        diffs = compare_json(a, b)
        assert len(diffs) == 3


class TestCompareSnapshot:
    """Test the compare_snapshot wrapper."""

    def test_pass_result(self):
        result = compare_snapshot("test-1", {"a": 1}, {"a": 1})
        assert result.passed is True
        assert result.query_id == "test-1"
        assert result.diffs == []

    def test_fail_result(self):
        result = compare_snapshot("test-2", {"a": 1}, {"a": 2})
        assert result.passed is False
        assert result.query_id == "test-2"
        assert len(result.diffs) == 1


class TestComparisonResultSummary:
    """Test ComparisonResult.summary() method."""

    def test_pass_summary(self):
        result = ComparisonResult(query_id="q1", passed=True)
        assert result.summary() == "q1: PASS"

    def test_fail_summary(self):
        result = ComparisonResult(
            query_id="q2",
            passed=False,
            diffs=[FieldDiff("$.x", 1, 2, "value_mismatch")],
        )
        assert result.summary() == "q2: FAIL (1 diffs)"


class TestFormatDiffReport:
    """Test format_diff_report output formatting."""

    def test_pass_report(self):
        result = ComparisonResult(query_id="q1", passed=True)
        report = format_diff_report(result)
        assert report == "q1: PASS"

    def test_missing_diff(self):
        result = ComparisonResult(
            query_id="q1",
            passed=False,
            diffs=[FieldDiff("$.key", "expected_val", None, "missing")],
        )
        report = format_diff_report(result)
        assert "MISSING" in report
        assert "$.key" in report

    def test_extra_diff(self):
        result = ComparisonResult(
            query_id="q1",
            passed=False,
            diffs=[FieldDiff("$.extra", None, "unexpected", "extra")],
        )
        report = format_diff_report(result)
        assert "EXTRA" in report
        assert "$.extra" in report

    def test_value_mismatch_diff(self):
        result = ComparisonResult(
            query_id="q1",
            passed=False,
            diffs=[FieldDiff("$.val", 1, 2, "value_mismatch")],
        )
        report = format_diff_report(result)
        assert "DIFF" in report
        assert "$.val" in report

    def test_type_mismatch_diff(self):
        result = ComparisonResult(
            query_id="q1",
            passed=False,
            diffs=[FieldDiff("$.val", "str", [1], "type_mismatch")],
        )
        report = format_diff_report(result)
        assert "TYPE" in report
        assert "$.val" in report

    def test_max_diffs_truncation(self):
        diffs = [
            FieldDiff(f"$.field{i}", i, i + 100, "value_mismatch") for i in range(30)
        ]
        result = ComparisonResult(query_id="q1", passed=False, diffs=diffs)
        report = format_diff_report(result, max_diffs=5)
        assert "... and 25 more diffs" in report
        # Should only show 5 diff lines plus summary plus truncation message
        lines = report.strip().split("\n")
        assert len(lines) == 7  # 1 summary + 5 diffs + 1 truncation

    def test_default_max_diffs_is_20(self):
        diffs = [
            FieldDiff(f"$.field{i}", i, i + 100, "value_mismatch") for i in range(25)
        ]
        result = ComparisonResult(query_id="q1", passed=False, diffs=diffs)
        report = format_diff_report(result)
        assert "... and 5 more diffs" in report
