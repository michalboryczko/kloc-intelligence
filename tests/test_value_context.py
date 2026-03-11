"""Tests for Value context orchestrators.

All tests are pure unit tests with mocked QueryRunner -- no Neo4j required.

Coverage:
- build_value_consumer_chain: Part 1 (receiver grouping), Part 2 (standalone),
  Part 3 (direct arguments), deduplication, cycle detection, sorting, limit
- cross_into_callee: parameter FQN matching, return value path
- cross_into_callers_via_return: all 6 safety guards
- build_value_source_chain: assigned_from path, result-is-source, parameter delegation
- build_parameter_uses: argument edge matching, depth expansion
- Internal helpers: _build_callee_display, _infer_ref_type_from_call_kind, etc.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.models.results import ContextEntry
from src.orchestration.value_context import (
    build_value_consumer_chain,
    build_value_source_chain,
    build_parameter_uses,
    cross_into_callee,
    cross_into_callers_via_return,
    _build_callee_display,
    _infer_ref_type_from_call_kind,
    _resolve_on_kind,
    _build_arguments_from_records,
)
from src.db.queries.context_value import (
    Q1_RECEIVER_CHAIN,
    Q2_DIRECT_ARGUMENTS,
    Q4_ARGUMENT_PARAMS,
    Q5_RESOLVE_PARAM,
    Q6_LOCAL_FOR_RESULT,
    Q7_TYPE_OF,
    Q8_METHOD_CALLERS,
    Q9_SOURCE_CHAIN,
    Q10_PARAMETER_USES,
    Q11_CALL_ARGUMENTS,
    Q12_CONTAINING_METHOD,
)


# =============================================================================
# Fixtures / Factories
# =============================================================================


def make_runner(execute_map=None, execute_single_map=None):
    """Return a MagicMock QueryRunner with configurable responses.

    execute_map: dict mapping query constant -> return value for execute()
    execute_single_map: dict mapping query constant -> return value for execute_single()
    """
    runner = MagicMock()

    execute_map = execute_map or {}
    execute_single_map = execute_single_map or {}

    def mock_execute(query, **kwargs):
        for q_const, result in execute_map.items():
            if q_const.strip() in query.strip() or query.strip() in q_const.strip():
                if callable(result):
                    return result(**kwargs)
                return result
        return []

    def mock_execute_single(query, **kwargs):
        for q_const, result in execute_single_map.items():
            if q_const.strip() in query.strip() or query.strip() in q_const.strip():
                if callable(result):
                    return result(**kwargs)
                return result
        return None

    runner.execute.side_effect = mock_execute
    runner.execute_single.side_effect = mock_execute_single
    return runner


# =============================================================================
# _build_callee_display
# =============================================================================


class TestBuildCalleeDisplay:
    """Tests for callee display name formatting."""

    def test_method_adds_parens(self):
        assert _build_callee_display("save", "Method") == "save()"

    def test_property_adds_dollar(self):
        assert _build_callee_display("name", "Property") == "$name"

    def test_property_with_dollar_kept(self):
        assert _build_callee_display("$name", "Property") == "$name"

    def test_none_name_returns_none(self):
        assert _build_callee_display(None, "Method") is None

    def test_other_kind_returns_as_is(self):
        assert _build_callee_display("ACTIVE", "Constant") == "ACTIVE"


# =============================================================================
# _infer_ref_type_from_call_kind
# =============================================================================


class TestInferRefType:
    """Tests for reference type inference from call/target kinds."""

    def test_constructor(self):
        assert _infer_ref_type_from_call_kind("constructor", "Method") == "instantiation"

    def test_method_target(self):
        assert _infer_ref_type_from_call_kind(None, "Method") == "method_call"

    def test_property_target(self):
        assert _infer_ref_type_from_call_kind(None, "Property") == "property_access"

    def test_function_target(self):
        assert _infer_ref_type_from_call_kind(None, "Function") == "function_call"

    def test_none_none_returns_none(self):
        assert _infer_ref_type_from_call_kind(None, None) is None


# =============================================================================
# _resolve_on_kind
# =============================================================================


class TestResolveOnKind:
    """Tests for receiver on_kind mapping."""

    def test_parameter(self):
        assert _resolve_on_kind("parameter") == "param"

    def test_local(self):
        assert _resolve_on_kind("local") == "local"

    def test_self(self):
        assert _resolve_on_kind("self") == "self"

    def test_result(self):
        assert _resolve_on_kind("result") == "property"

    def test_none(self):
        assert _resolve_on_kind(None) is None

    def test_unknown_passthrough(self):
        assert _resolve_on_kind("static") == "static"


# =============================================================================
# _build_arguments_from_records
# =============================================================================


class TestBuildArgumentsFromRecords:
    """Tests for argument building from Q11 records."""

    def test_empty_records(self):
        runner = make_runner(execute_map={Q11_CALL_ARGUMENTS: []})
        infos = _build_arguments_from_records(runner, "call:1")
        assert infos == []

    def test_single_argument(self):
        runner = make_runner(execute_map={
            Q11_CALL_ARGUMENTS: [
                {"position": 0, "expression": "$id", "value_kind": "local",
                 "value_type": "int", "parameter": "param_fqn", "value_fqn": None,
                 "value_id": "v1", "value_name": "$id"},
            ]
        })
        infos = _build_arguments_from_records(runner, "call:1")
        assert len(infos) == 1
        assert infos[0].position == 0
        assert infos[0].value_expr == "$id"
        assert infos[0].value_type == "int"

    def test_multiple_sorted_by_position(self):
        runner = make_runner(execute_map={
            Q11_CALL_ARGUMENTS: [
                {"position": 1, "expression": "$b", "value_kind": "local",
                 "value_type": None, "parameter": None, "value_fqn": None,
                 "value_id": "v2", "value_name": "$b"},
                {"position": 0, "expression": "$a", "value_kind": "local",
                 "value_type": None, "parameter": None, "value_fqn": None,
                 "value_id": "v1", "value_name": "$a"},
            ]
        })
        infos = _build_arguments_from_records(runner, "call:1")
        assert infos[0].position == 0
        assert infos[1].position == 1

    def test_skips_none_position(self):
        runner = make_runner(execute_map={
            Q11_CALL_ARGUMENTS: [
                {"position": None, "expression": "$x", "value_kind": "local",
                 "value_type": None, "parameter": None, "value_fqn": None,
                 "value_id": "v1", "value_name": "$x"},
            ]
        })
        infos = _build_arguments_from_records(runner, "call:1")
        assert infos == []


# =============================================================================
# build_value_consumer_chain — Part 1: Receiver grouping
# =============================================================================


class TestConsumerChainPart1ReceiverGrouping:
    """Tests for receiver edges grouped by downstream consumer Call."""

    def test_single_receiver_with_consumer(self):
        """Receiver edge -> result consumed as argument by another Call."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [
                    {
                        "access_call_id": "access:1",
                        "access_call_file": "src/Service.php",
                        "access_call_line": 10,
                        "access_call_kind": "property_access",
                        "target_id": "prop:id",
                        "target_fqn": "App\\Order::$id",
                        "target_name": "$id",
                        "target_kind": "Property",
                        "result_id": "result:1",
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "src/Service.php",
                        "consumer_call_line": 12,
                        "consumer_call_kind": None,
                        "consumer_target_id": "method:send",
                        "consumer_target_fqn": "App\\Notifier::send",
                        "consumer_target_name": "send",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": "send($to)",
                        "arg_position": 0,
                        "arg_expression": "$order->id",
                        "assigned_local_id": None,
                    }
                ],
                Q2_DIRECT_ARGUMENTS: [],
                Q11_CALL_ARGUMENTS: [
                    {"position": 0, "expression": "$order->id",
                     "value_kind": "result", "value_type": None,
                     "parameter": "to_fqn", "value_fqn": None,
                     "value_id": "v1", "value_name": None},
                ],
            },
        )
        entries = build_value_consumer_chain(runner, "val:order", 1, 1, 100)
        assert len(entries) == 1
        assert entries[0].fqn == "App\\Notifier::send"
        assert entries[0].kind == "Method"
        assert entries[0].ref_type == "method_call"

    def test_multiple_receivers_same_consumer(self):
        """Two property accesses both feed into the same consumer Call."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [
                    {
                        "access_call_id": "access:1",
                        "access_call_file": "f.php", "access_call_line": 10,
                        "access_call_kind": "property_access",
                        "target_id": "prop:id", "target_fqn": "Order::$id",
                        "target_name": "$id", "target_kind": "Property",
                        "result_id": "r:1",
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "f.php", "consumer_call_line": 15,
                        "consumer_call_kind": "constructor",
                        "consumer_target_id": "cls:Output",
                        "consumer_target_fqn": "App\\Output",
                        "consumer_target_name": "Output",
                        "consumer_target_kind": "Class",
                        "consumer_target_signature": None,
                        "arg_position": 0, "arg_expression": "$o->id",
                        "assigned_local_id": None,
                    },
                    {
                        "access_call_id": "access:2",
                        "access_call_file": "f.php", "access_call_line": 11,
                        "access_call_kind": "property_access",
                        "target_id": "prop:name", "target_fqn": "Order::$name",
                        "target_name": "$name", "target_kind": "Property",
                        "result_id": "r:2",
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "f.php", "consumer_call_line": 15,
                        "consumer_call_kind": "constructor",
                        "consumer_target_id": "cls:Output",
                        "consumer_target_fqn": "App\\Output",
                        "consumer_target_name": "Output",
                        "consumer_target_kind": "Class",
                        "consumer_target_signature": None,
                        "arg_position": 1, "arg_expression": "$o->name",
                        "assigned_local_id": None,
                    },
                ],
                Q2_DIRECT_ARGUMENTS: [],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:order", 1, 1, 100)
        # Both accesses grouped into single consumer entry
        assert len(entries) == 1
        assert entries[0].ref_type == "instantiation"

    def test_constructor_ref_type(self):
        """Consumer with call_kind='constructor' infers instantiation."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [
                    {
                        "access_call_id": "access:1",
                        "access_call_file": "f.php", "access_call_line": 5,
                        "access_call_kind": None,
                        "target_id": "p:1", "target_fqn": "X::$id",
                        "target_name": "$id", "target_kind": "Property",
                        "result_id": "r:1",
                        "consumer_call_id": "c:1",
                        "consumer_call_file": "f.php", "consumer_call_line": 10,
                        "consumer_call_kind": "constructor",
                        "consumer_target_id": "cls:Y",
                        "consumer_target_fqn": "App\\Y",
                        "consumer_target_name": "Y",
                        "consumer_target_kind": "Class",
                        "consumer_target_signature": None,
                        "arg_position": 0, "arg_expression": "$x->id",
                        "assigned_local_id": None,
                    },
                ],
                Q2_DIRECT_ARGUMENTS: [],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:x", 1, 1, 100)
        assert entries[0].ref_type == "instantiation"


# =============================================================================
# build_value_consumer_chain — Part 2: Standalone accesses
# =============================================================================


class TestConsumerChainPart2Standalone:
    """Tests for standalone property accesses (not consumed as arguments)."""

    def test_standalone_property_access(self):
        """Property access whose result is not consumed by another Call."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [
                    {
                        "access_call_id": "access:1",
                        "access_call_file": "src/Svc.php",
                        "access_call_line": 20,
                        "access_call_kind": "property_access",
                        "target_id": "prop:email",
                        "target_fqn": "User::$email",
                        "target_name": "$email",
                        "target_kind": "Property",
                        "result_id": "r:1",
                        "consumer_call_id": None,
                        "consumer_call_file": None,
                        "consumer_call_line": None,
                        "consumer_call_kind": None,
                        "consumer_target_id": None,
                        "consumer_target_fqn": None,
                        "consumer_target_name": None,
                        "consumer_target_kind": None,
                        "consumer_target_signature": None,
                        "arg_position": None,
                        "arg_expression": None,
                        "assigned_local_id": None,
                    },
                ],
                Q2_DIRECT_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:user", 1, 1, 100)
        assert len(entries) == 1
        assert entries[0].fqn == "User::$email"
        assert entries[0].ref_type == "property_access"

    def test_standalone_with_assigned_local(self):
        """Access whose result is assigned to a local variable (still standalone)."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [
                    {
                        "access_call_id": "access:1",
                        "access_call_file": "f.php", "access_call_line": 5,
                        "access_call_kind": "property_access",
                        "target_id": "prop:x", "target_fqn": "A::$x",
                        "target_name": "$x", "target_kind": "Property",
                        "result_id": "r:1",
                        "consumer_call_id": None,
                        "consumer_call_file": None, "consumer_call_line": None,
                        "consumer_call_kind": None,
                        "consumer_target_id": None,
                        "consumer_target_fqn": None,
                        "consumer_target_name": None,
                        "consumer_target_kind": None,
                        "consumer_target_signature": None,
                        "arg_position": None, "arg_expression": None,
                        "assigned_local_id": "local:1",
                    },
                ],
                Q2_DIRECT_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:a", 1, 1, 100)
        assert len(entries) == 1
        assert entries[0].node_id == "prop:x"


# =============================================================================
# build_value_consumer_chain — Part 3: Direct arguments
# =============================================================================


class TestConsumerChainPart3DirectArguments:
    """Tests for Value passed directly as argument to a Call."""

    def test_direct_argument(self):
        """Value used directly as argument (no receiver step)."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [],
                Q2_DIRECT_ARGUMENTS: [
                    {
                        "consumer_call_id": "call:1",
                        "consumer_call_file": "src/App.php",
                        "consumer_call_line": 30,
                        "consumer_call_kind": None,
                        "consumer_target_id": "method:process",
                        "consumer_target_fqn": "App\\Svc::process",
                        "consumer_target_name": "process",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": "process($input)",
                    },
                ],
                Q11_CALL_ARGUMENTS: [
                    {"position": 0, "expression": "$data", "value_kind": "local",
                     "value_type": None, "parameter": None, "value_fqn": None,
                     "value_id": "v1", "value_name": "$data"},
                ],
            },
        )
        entries = build_value_consumer_chain(runner, "val:data", 1, 1, 100)
        assert len(entries) == 1
        assert entries[0].fqn == "App\\Svc::process"
        assert entries[0].kind == "Method"
        assert entries[0].ref_type == "method_call"
        assert len(entries[0].arguments) == 1


# =============================================================================
# Deduplication and cycle detection
# =============================================================================


class TestConsumerChainDedup:
    """Tests for seen_calls deduplication and visited cycle detection."""

    def test_same_consumer_call_deduplicated(self):
        """Two receiver records referencing the same consumer_call_id produce one entry."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [
                    {
                        "access_call_id": "access:1",
                        "access_call_file": "f.php", "access_call_line": 5,
                        "access_call_kind": None,
                        "target_id": "p:1", "target_fqn": "X::$a",
                        "target_name": "$a", "target_kind": "Property",
                        "result_id": "r:1",
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "f.php", "consumer_call_line": 10,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:1",
                        "consumer_target_fqn": "M::do",
                        "consumer_target_name": "do",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                        "arg_position": 0, "arg_expression": "$x",
                        "assigned_local_id": None,
                    },
                    {
                        "access_call_id": "access:2",
                        "access_call_file": "f.php", "access_call_line": 6,
                        "access_call_kind": None,
                        "target_id": "p:2", "target_fqn": "X::$b",
                        "target_name": "$b", "target_kind": "Property",
                        "result_id": "r:2",
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "f.php", "consumer_call_line": 10,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:1",
                        "consumer_target_fqn": "M::do",
                        "consumer_target_name": "do",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                        "arg_position": 1, "arg_expression": "$y",
                        "assigned_local_id": None,
                    },
                ],
                Q2_DIRECT_ARGUMENTS: [],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:x", 1, 1, 100)
        assert len(entries) == 1

    def test_cycle_detection_prevents_revisit(self):
        """Value already in visited set should produce empty result."""
        runner = make_runner(execute_map={Q1_RECEIVER_CHAIN: [], Q2_DIRECT_ARGUMENTS: []})
        visited = {"val:cycle"}
        entries = build_value_consumer_chain(runner, "val:cycle", 1, 3, 100, visited=visited)
        assert entries == []

    def test_value_added_to_visited(self):
        """Value ID should be added to visited after processing."""
        runner = make_runner(execute_map={Q1_RECEIVER_CHAIN: [], Q2_DIRECT_ARGUMENTS: []})
        visited: set[str] = set()
        build_value_consumer_chain(runner, "val:new", 1, 1, 100, visited=visited)
        assert "val:new" in visited

    def test_depth_exceeded_returns_empty(self):
        """Depth > max_depth should return empty."""
        runner = make_runner()
        entries = build_value_consumer_chain(runner, "val:x", 5, 3, 100)
        assert entries == []


# =============================================================================
# Sorting and Limit
# =============================================================================


class TestConsumerChainSortingAndLimit:
    """Tests for entry sorting by (file, line) and limit enforcement."""

    def test_entries_sorted_by_file_then_line(self):
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [],
                Q2_DIRECT_ARGUMENTS: [
                    {
                        "consumer_call_id": "c:2",
                        "consumer_call_file": "src/B.php",
                        "consumer_call_line": 5,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:2",
                        "consumer_target_fqn": "B::go",
                        "consumer_target_name": "go",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    },
                    {
                        "consumer_call_id": "c:1",
                        "consumer_call_file": "src/A.php",
                        "consumer_call_line": 10,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:1",
                        "consumer_target_fqn": "A::run",
                        "consumer_target_name": "run",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    },
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:x", 1, 1, 100)
        assert len(entries) == 2
        assert entries[0].file == "src/A.php"
        assert entries[1].file == "src/B.php"

    def test_limit_caps_entries(self):
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [],
                Q2_DIRECT_ARGUMENTS: [
                    {
                        "consumer_call_id": f"c:{i}",
                        "consumer_call_file": f"src/{i}.php",
                        "consumer_call_line": i,
                        "consumer_call_kind": None,
                        "consumer_target_id": f"m:{i}",
                        "consumer_target_fqn": f"M{i}::run",
                        "consumer_target_name": "run",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    }
                    for i in range(10)
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:x", 1, 1, 3)
        assert len(entries) == 3


# =============================================================================
# cross_into_callee
# =============================================================================


class TestCrossIntoCallee:
    """Tests for cross_into_callee method boundary crossing."""

    def test_parameter_fqn_matching_adds_children(self):
        """Argument with parameter FQN resolves to Value(parameter) and recurses."""
        entry = ContextEntry(depth=1, node_id="m:target", fqn="Target::process", children=[])

        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q4_ARGUMENT_PARAMS.strip() in q:
                return [{"parameter_fqn": "Target::process::$input", "value_id": "v:arg", "position": 0, "expression": "$data"}]
            if Q1_RECEIVER_CHAIN.strip() in q:
                return [
                    {
                        "access_call_id": "inner:call:1",
                        "access_call_file": "f.php", "access_call_line": 20,
                        "access_call_kind": None,
                        "target_id": "p:inner", "target_fqn": "X::$val",
                        "target_name": "$val", "target_kind": "Property",
                        "result_id": None,
                        "consumer_call_id": None, "consumer_call_file": None,
                        "consumer_call_line": None, "consumer_call_kind": None,
                        "consumer_target_id": None, "consumer_target_fqn": None,
                        "consumer_target_name": None, "consumer_target_kind": None,
                        "consumer_target_signature": None,
                        "arg_position": None, "arg_expression": None,
                        "assigned_local_id": None,
                    },
                ]
            if Q2_DIRECT_ARGUMENTS.strip() in q:
                return []
            return []

        def mock_execute_single(query, **kwargs):
            q = query.strip()
            if Q5_RESOLVE_PARAM.strip() in q:
                return {"value_id": "val:param:input"}
            if Q6_LOCAL_FOR_RESULT.strip() in q:
                return {"result_id": None, "local_id": None}
            return None

        runner = MagicMock()
        runner.execute.side_effect = mock_execute
        runner.execute_single.side_effect = mock_execute_single

        visited: set[str] = set()
        cross_into_callee(
            runner, "call:outer", "m:target", entry, 1, 3, 100, visited,
        )
        assert len(entry.children) >= 1
        # Children should have crossed_from set
        assert entry.children[0].crossed_from == "Target::process::$input"

    def test_return_value_path_follows_local(self):
        """When call produces a local variable, trace that local."""
        entry = ContextEntry(depth=1, node_id="m:target", fqn="T::run", children=[])

        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q4_ARGUMENT_PARAMS.strip() in q:
                return []
            if Q1_RECEIVER_CHAIN.strip() in q:
                return []
            if Q2_DIRECT_ARGUMENTS.strip() in q:
                return []
            return []

        def mock_execute_single(query, **kwargs):
            q = query.strip()
            if Q6_LOCAL_FOR_RESULT.strip() in q:
                return {"result_id": "r:1", "local_id": "local:1"}
            return None

        runner = MagicMock()
        runner.execute.side_effect = mock_execute
        runner.execute_single.side_effect = mock_execute_single

        visited: set[str] = set()
        cross_into_callee(
            runner, "call:outer", "m:target", entry, 1, 3, 100, visited,
        )
        # local:1 should be added to visited (even if no children found)
        assert "local:1" in visited


# =============================================================================
# cross_into_callers_via_return — Safety Guards
# =============================================================================


class TestCrossIntoCallersViaReturn:
    """Tests for all safety guards in cross_into_callers_via_return."""

    def test_guard_depth_budget(self):
        """depth >= max_depth stops expansion."""
        entry = ContextEntry(depth=3, node_id="x", fqn="X", children=[])
        runner = MagicMock()
        cross_into_callers_via_return(
            runner, "call:1", entry, 3, 3, 100, set(),
        )
        assert entry.children == []
        # Should not call any queries
        runner.execute_single.assert_not_called()

    def test_guard_crossing_limit(self):
        """crossing_count >= max_crossings stops expansion."""
        entry = ContextEntry(depth=1, node_id="x", fqn="X", children=[])
        runner = MagicMock()
        cross_into_callers_via_return(
            runner, "call:1", entry, 1, 3, 100, set(),
            crossing_count=10, max_crossings=10,
        )
        assert entry.children == []

    def test_guard_no_result_id(self):
        """No result from consumer call stops expansion."""
        entry = ContextEntry(depth=1, node_id="x", fqn="X", children=[])
        runner = MagicMock()
        runner.execute_single.return_value = {"result_id": None, "local_id": None}
        cross_into_callers_via_return(
            runner, "call:1", entry, 1, 3, 100, set(),
        )
        assert entry.children == []

    def test_guard_has_local_assignment(self):
        """If result has a local assignment, it's not an inline return."""
        entry = ContextEntry(depth=1, node_id="x", fqn="X", children=[])

        call_count = [0]

        def mock_single(query, **kwargs):
            q = query.strip()
            if Q6_LOCAL_FOR_RESULT.strip() in q:
                return {"result_id": "r:1", "local_id": "local:existing"}
            call_count[0] += 1
            return None

        runner = MagicMock()
        runner.execute_single.side_effect = mock_single
        cross_into_callers_via_return(
            runner, "call:1", entry, 1, 3, 100, set(),
        )
        assert entry.children == []

    def test_guard_no_type_info(self):
        """No type_of on consumer result -> conservative stop."""
        entry = ContextEntry(depth=1, node_id="x", fqn="X", children=[])

        def mock_single(query, **kwargs):
            q = query.strip()
            if Q6_LOCAL_FOR_RESULT.strip() in q:
                return {"result_id": "r:1", "local_id": None}
            if Q7_TYPE_OF.strip() in q:
                return None
            return None

        runner = MagicMock()
        runner.execute_single.side_effect = mock_single
        cross_into_callers_via_return(
            runner, "call:1", entry, 1, 3, 100, set(),
        )
        assert entry.children == []

    def test_guard_method_cycle_prevention(self):
        """Method already in visited as return_crossing stops re-entry."""
        entry = ContextEntry(depth=1, node_id="x", fqn="X", children=[])

        def mock_single(query, **kwargs):
            q = query.strip()
            if Q6_LOCAL_FOR_RESULT.strip() in q:
                return {"result_id": "r:1", "local_id": None}
            if Q7_TYPE_OF.strip() in q:
                return {"type_id": "type:Foo", "type_fqn": "Foo"}
            if Q12_CONTAINING_METHOD.strip() in q:
                return {"method_id": "m:container", "method_fqn": "C::run", "method_kind": "Method"}
            return None

        runner = MagicMock()
        runner.execute_single.side_effect = mock_single
        runner.execute.return_value = []

        visited = {"return_crossing:m:container"}
        cross_into_callers_via_return(
            runner, "call:1", entry, 1, 3, 100, visited,
        )
        assert entry.children == []

    def test_guard_type_mismatch_skips_caller(self):
        """Caller local type doesn't match consumer result type -> skip."""
        entry = ContextEntry(depth=1, node_id="x", fqn="X", children=[])

        def mock_single(query, **kwargs):
            q = query.strip()
            if Q6_LOCAL_FOR_RESULT.strip() in q:
                return {"result_id": "r:1", "local_id": None}
            if Q7_TYPE_OF.strip() in q:
                return {"type_id": "type:Foo", "type_fqn": "Foo"}
            if Q12_CONTAINING_METHOD.strip() in q:
                return {"method_id": "m:container", "method_fqn": "C::run", "method_kind": "Method"}
            return None

        runner = MagicMock()
        runner.execute_single.side_effect = mock_single
        runner.execute.return_value = [
            {
                "caller_call_id": "caller:1",
                "caller_result_id": "cr:1",
                "caller_local_id": "cl:1",
                "caller_type_id": "type:Bar",  # Mismatch! Foo != Bar
                "caller_method_id": "cm:1",
                "caller_method_fqn": "Caller::exec",
                "caller_method_kind": "Method",
            }
        ]

        visited: set[str] = set()
        cross_into_callers_via_return(
            runner, "call:1", entry, 1, 3, 100, visited,
        )
        assert entry.children == []
        # method_key should NOT be added to visited since no match found
        assert "return_crossing:m:container" not in visited

    def test_type_match_adds_to_visited_and_crosses(self):
        """Type match triggers lazy method marking and recursion."""
        entry = ContextEntry(depth=1, node_id="x", fqn="X", children=[])

        def mock_single(query, **kwargs):
            q = query.strip()
            if Q6_LOCAL_FOR_RESULT.strip() in q:
                if kwargs.get("call_id") == "call:main":
                    return {"result_id": "r:1", "local_id": None}
                return {"result_id": None, "local_id": None}
            if Q7_TYPE_OF.strip() in q:
                return {"type_id": "type:Foo", "type_fqn": "Foo"}
            if Q12_CONTAINING_METHOD.strip() in q:
                return {"method_id": "m:container", "method_fqn": "C::run", "method_kind": "Method"}
            return None

        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q8_METHOD_CALLERS.strip() in q:
                return [
                    {
                        "caller_call_id": "caller:1",
                        "caller_result_id": "cr:1",
                        "caller_local_id": "cl:1",
                        "caller_type_id": "type:Foo",  # Match!
                        "caller_method_id": "cm:1",
                        "caller_method_fqn": "Caller::exec",
                        "caller_method_kind": "Method",
                    }
                ]
            if Q1_RECEIVER_CHAIN.strip() in q:
                return []
            if Q2_DIRECT_ARGUMENTS.strip() in q:
                return []
            return []

        runner = MagicMock()
        runner.execute_single.side_effect = mock_single
        runner.execute.side_effect = mock_execute

        visited: set[str] = set()
        cross_into_callers_via_return(
            runner, "call:main", entry, 1, 3, 100, visited,
        )
        # Method key should be added (lazy marking)
        assert "return_crossing:m:container" in visited


# =============================================================================
# build_value_source_chain
# =============================================================================


class TestValueSourceChain:
    """Tests for source chain traversal."""

    def test_cycle_detection(self):
        """Value already visited returns empty."""
        runner = MagicMock()
        visited = {"val:cycle"}
        entries = build_value_source_chain(runner, "val:cycle", 1, 3, 100, visited)
        assert entries == []

    def test_depth_exceeded_returns_empty(self):
        """Depth > max_depth returns empty."""
        runner = MagicMock()
        entries = build_value_source_chain(runner, "val:x", 5, 3, 100)
        assert entries == []

    def test_parameter_delegates_to_parameter_uses(self):
        """Parameter value kind delegates to build_parameter_uses."""
        runner = make_runner(
            execute_map={
                Q9_SOURCE_CHAIN: [
                    {
                        "value_kind": "parameter",
                        "value_fqn": "A::run::$input",
                        "source_id": None, "source_kind": None,
                        "call_id": None, "call_file": None, "call_line": None,
                        "call_kind": None, "callee_id": None, "callee_fqn": None,
                        "callee_name": None, "callee_kind": None,
                        "callee_signature": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                    },
                ],
                Q10_PARAMETER_USES: [],
            },
        )
        entries = build_value_source_chain(runner, "val:param", 1, 3, 100)
        assert entries == []  # No callers found, but delegation happened

    def test_assigned_from_source_chain(self):
        """Standard source chain: assigned_from -> produces -> Call -> callee."""
        runner = make_runner(
            execute_map={
                Q9_SOURCE_CHAIN: [
                    {
                        "value_kind": "local",
                        "value_fqn": "A::run::$order",
                        "source_id": "source:1",
                        "source_kind": "result",
                        "call_id": "call:save",
                        "call_file": "src/Svc.php",
                        "call_line": 15,
                        "call_kind": None,
                        "callee_id": "m:save",
                        "callee_fqn": "Repo::save",
                        "callee_name": "save",
                        "callee_kind": "Method",
                        "callee_signature": "save($entity)",
                        "recv_value_kind": "parameter",
                        "recv_name": "$repo",
                        "recv_prop_fqn": None,
                    },
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_source_chain(runner, "val:order", 1, 3, 100)
        assert len(entries) == 1
        assert entries[0].fqn == "Repo::save"
        assert entries[0].kind == "Method"
        assert entries[0].member_ref is not None
        assert entries[0].member_ref.access_chain == "$repo"
        assert entries[0].member_ref.on_kind == "param"

    def test_result_value_is_source(self):
        """When no assigned_from and value is result, it IS the source."""
        runner = make_runner(
            execute_map={
                Q9_SOURCE_CHAIN: [
                    {
                        "value_kind": "result",
                        "value_fqn": "result_fqn",
                        "source_id": None,
                        "source_kind": None,
                        "call_id": "call:create",
                        "call_file": "src/Factory.php",
                        "call_line": 20,
                        "call_kind": "constructor",
                        "callee_id": "cls:Order",
                        "callee_fqn": "App\\Order",
                        "callee_name": "Order",
                        "callee_kind": "Class",
                        "callee_signature": None,
                        "recv_value_kind": None,
                        "recv_name": None,
                        "recv_prop_fqn": None,
                    },
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_source_chain(runner, "val:result", 1, 3, 100)
        assert len(entries) == 1
        assert entries[0].fqn == "App\\Order"

    def test_no_callee_returns_empty(self):
        """No callee_id means we can't trace further."""
        runner = make_runner(
            execute_map={
                Q9_SOURCE_CHAIN: [
                    {
                        "value_kind": "local",
                        "value_fqn": "A::$x",
                        "source_id": "s:1",
                        "source_kind": "result",
                        "call_id": "c:1",
                        "call_file": "f.php", "call_line": 5,
                        "call_kind": None,
                        "callee_id": None,
                        "callee_fqn": None,
                        "callee_name": None, "callee_kind": None,
                        "callee_signature": None,
                        "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                    },
                ],
            },
        )
        entries = build_value_source_chain(runner, "val:x", 1, 3, 100)
        assert entries == []

    def test_receiver_prop_fqn_sets_property_on_kind(self):
        """When receiver has a property FQN, on_kind should be 'property'."""
        runner = make_runner(
            execute_map={
                Q9_SOURCE_CHAIN: [
                    {
                        "value_kind": "local",
                        "value_fqn": "A::$x",
                        "source_id": "s:1",
                        "source_kind": "result",
                        "call_id": "c:1",
                        "call_file": "f.php", "call_line": 5,
                        "call_kind": None,
                        "callee_id": "m:get",
                        "callee_fqn": "Svc::get",
                        "callee_name": "get",
                        "callee_kind": "Method",
                        "callee_signature": "get()",
                        "recv_value_kind": "result",
                        "recv_name": "$this->repo",
                        "recv_prop_fqn": "Svc::$repo",
                    },
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_source_chain(runner, "val:x", 1, 3, 100)
        assert len(entries) == 1
        assert entries[0].member_ref.on_kind == "property"
        assert entries[0].member_ref.access_chain == "$this->repo"


# =============================================================================
# build_parameter_uses
# =============================================================================


class TestParameterUses:
    """Tests for parameter uses via argument edge matching."""

    def test_finds_callers_by_param_fqn(self):
        """Finds Calls whose argument edges match the parameter FQN."""
        runner = make_runner(
            execute_map={
                Q10_PARAMETER_USES: [
                    {
                        "call_id": "call:1",
                        "call_file": "src/App.php",
                        "call_line": 25,
                        "value_id": "val:arg1",
                        "value_kind": "local",
                        "value_name": "$data",
                        "value_fqn": "App::run::$data",
                        "scope_id": "m:run",
                        "scope_fqn": "App::run",
                        "scope_kind": "Method",
                        "scope_signature": "run($input)",
                        "position": 0,
                        "expression": "$data",
                    },
                ],
            },
        )
        entries = build_parameter_uses(
            runner, "val:param:input", "Svc::process::$input", 1, 1, 100, set()
        )
        assert len(entries) == 1
        assert entries[0].fqn == "App::run"
        assert entries[0].crossed_from == "Svc::process::$input"

    def test_sorted_by_file_and_line(self):
        runner = make_runner(
            execute_map={
                Q10_PARAMETER_USES: [
                    {
                        "call_id": "c:2", "call_file": "src/B.php", "call_line": 5,
                        "value_id": "v:2", "value_kind": "local",
                        "value_name": "$b", "value_fqn": None,
                        "scope_id": "m:b", "scope_fqn": "B::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "position": 0, "expression": "$b",
                    },
                    {
                        "call_id": "c:1", "call_file": "src/A.php", "call_line": 10,
                        "value_id": "v:1", "value_kind": "local",
                        "value_name": "$a", "value_fqn": None,
                        "scope_id": "m:a", "scope_fqn": "A::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "position": 0, "expression": "$a",
                    },
                ],
            },
        )
        entries = build_parameter_uses(
            runner, "val:p", "X::$p", 1, 1, 100, set()
        )
        assert entries[0].file == "src/A.php"
        assert entries[1].file == "src/B.php"

    def test_limit_applied(self):
        runner = make_runner(
            execute_map={
                Q10_PARAMETER_USES: [
                    {
                        "call_id": f"c:{i}", "call_file": f"f{i}.php", "call_line": i,
                        "value_id": f"v:{i}", "value_kind": "local",
                        "value_name": f"$x{i}", "value_fqn": None,
                        "scope_id": f"m:{i}", "scope_fqn": f"M{i}::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "position": 0, "expression": f"$x{i}",
                    }
                    for i in range(5)
                ],
            },
        )
        entries = build_parameter_uses(
            runner, "val:p", "X::$p", 1, 1, 2, set()
        )
        assert len(entries) == 2

    def test_depth_expansion_traces_argument_source(self):
        """At depth < max_depth, traces the caller's argument Value source."""
        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q10_PARAMETER_USES.strip() in q:
                return [
                    {
                        "call_id": "c:1", "call_file": "f.php", "call_line": 5,
                        "value_id": "val:caller_arg",
                        "value_kind": "local",
                        "value_name": "$data", "value_fqn": "A::run::$data",
                        "scope_id": "m:run", "scope_fqn": "A::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "position": 0, "expression": "$data",
                    },
                ]
            if Q9_SOURCE_CHAIN.strip() in q:
                return [
                    {
                        "value_kind": "local",
                        "value_fqn": "A::run::$data",
                        "source_id": "s:1", "source_kind": "result",
                        "call_id": "call:create",
                        "call_file": "f.php", "call_line": 3,
                        "call_kind": "constructor",
                        "callee_id": "cls:Order",
                        "callee_fqn": "App\\Order",
                        "callee_name": "Order",
                        "callee_kind": "Class",
                        "callee_signature": None,
                        "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                    },
                ]
            if Q11_CALL_ARGUMENTS.strip() in q:
                return []
            return []

        runner = MagicMock()
        runner.execute.side_effect = mock_execute
        runner.execute_single.return_value = None

        entries = build_parameter_uses(
            runner, "val:p", "X::$p", 1, 3, 100, set()
        )
        assert len(entries) == 1
        assert len(entries[0].children) == 1
        assert entries[0].children[0].fqn == "App\\Order"


# =============================================================================
# Consumer chain with cross-method callee expansion
# =============================================================================


class TestConsumerChainCrossMethodCallee:
    """Tests for depth expansion into callee methods."""

    def test_no_expansion_at_max_depth(self):
        """At depth == max_depth, no cross-method expansion occurs."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [],
                Q2_DIRECT_ARGUMENTS: [
                    {
                        "consumer_call_id": "c:1",
                        "consumer_call_file": "f.php",
                        "consumer_call_line": 5,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:process",
                        "consumer_target_fqn": "Svc::process",
                        "consumer_target_name": "process",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    },
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:x", 3, 3, 100)
        assert len(entries) == 1
        assert entries[0].children == []


# =============================================================================
# Mixed scenario tests
# =============================================================================


class TestConsumerChainMixed:
    """Tests for mixed scenarios combining Parts 1, 2, and 3."""

    def test_all_three_parts_combined(self):
        """Entries from all three parts appear in the result."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [
                    # Part 1: receiver with consumer
                    {
                        "access_call_id": "access:1",
                        "access_call_file": "f.php", "access_call_line": 5,
                        "access_call_kind": None,
                        "target_id": "p:1", "target_fqn": "X::$id",
                        "target_name": "$id", "target_kind": "Property",
                        "result_id": "r:1",
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "f.php", "consumer_call_line": 10,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:send",
                        "consumer_target_fqn": "N::send",
                        "consumer_target_name": "send",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                        "arg_position": 0, "arg_expression": "$x->id",
                        "assigned_local_id": None,
                    },
                    # Part 2: standalone access
                    {
                        "access_call_id": "access:2",
                        "access_call_file": "f.php", "access_call_line": 15,
                        "access_call_kind": "property_access",
                        "target_id": "p:2", "target_fqn": "X::$name",
                        "target_name": "$name", "target_kind": "Property",
                        "result_id": "r:2",
                        "consumer_call_id": None,
                        "consumer_call_file": None, "consumer_call_line": None,
                        "consumer_call_kind": None,
                        "consumer_target_id": None,
                        "consumer_target_fqn": None,
                        "consumer_target_name": None,
                        "consumer_target_kind": None,
                        "consumer_target_signature": None,
                        "arg_position": None, "arg_expression": None,
                        "assigned_local_id": None,
                    },
                ],
                Q2_DIRECT_ARGUMENTS: [
                    # Part 3: direct argument
                    {
                        "consumer_call_id": "call:3",
                        "consumer_call_file": "f.php",
                        "consumer_call_line": 20,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:log",
                        "consumer_target_fqn": "Logger::log",
                        "consumer_target_name": "log",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    },
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:x", 1, 1, 100)
        assert len(entries) == 3
        fqns = [e.fqn for e in entries]
        assert "N::send" in fqns
        assert "X::$name" in fqns
        assert "Logger::log" in fqns

    def test_seen_calls_prevents_cross_part_duplicates(self):
        """If a call appears in both Part 1 and Part 3, it's deduplicated."""
        runner = make_runner(
            execute_map={
                Q1_RECEIVER_CHAIN: [
                    {
                        "access_call_id": "access:1",
                        "access_call_file": "f.php", "access_call_line": 5,
                        "access_call_kind": None,
                        "target_id": "p:1", "target_fqn": "X::$id",
                        "target_name": "$id", "target_kind": "Property",
                        "result_id": "r:1",
                        "consumer_call_id": "shared:call",
                        "consumer_call_file": "f.php", "consumer_call_line": 10,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:do",
                        "consumer_target_fqn": "S::do",
                        "consumer_target_name": "do",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                        "arg_position": 0, "arg_expression": "$x",
                        "assigned_local_id": None,
                    },
                ],
                Q2_DIRECT_ARGUMENTS: [
                    {
                        "consumer_call_id": "shared:call",  # Same call
                        "consumer_call_file": "f.php",
                        "consumer_call_line": 10,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:do",
                        "consumer_target_fqn": "S::do",
                        "consumer_target_name": "do",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    },
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_consumer_chain(runner, "val:x", 1, 1, 100)
        assert len(entries) == 1

    def test_empty_value_returns_empty_list(self):
        """Value with no edges produces empty result."""
        runner = make_runner(
            execute_map={Q1_RECEIVER_CHAIN: [], Q2_DIRECT_ARGUMENTS: []},
        )
        entries = build_value_consumer_chain(runner, "val:empty", 1, 3, 100)
        assert entries == []

    def test_default_max_crossings(self):
        """max_crossings defaults to min(max_depth, 10)."""
        runner = make_runner(
            execute_map={Q1_RECEIVER_CHAIN: [], Q2_DIRECT_ARGUMENTS: []},
        )
        # With max_depth=5, max_crossings should default to 5
        entries = build_value_consumer_chain(runner, "val:x", 1, 5, 100)
        assert entries == []  # Just checking it doesn't error


# =============================================================================
# Source chain with constructor kind
# =============================================================================


class TestSourceChainConstructor:
    """Tests for source chain with constructor call kind."""

    def test_constructor_infers_instantiation(self):
        runner = make_runner(
            execute_map={
                Q9_SOURCE_CHAIN: [
                    {
                        "value_kind": "local",
                        "value_fqn": "Svc::run::$order",
                        "source_id": "s:1",
                        "source_kind": "result",
                        "call_id": "call:new",
                        "call_file": "src/Svc.php",
                        "call_line": 10,
                        "call_kind": "constructor",
                        "callee_id": "cls:Order",
                        "callee_fqn": "App\\Order",
                        "callee_name": "Order",
                        "callee_kind": "Class",
                        "callee_signature": None,
                        "recv_value_kind": None,
                        "recv_name": None,
                        "recv_prop_fqn": None,
                    },
                ],
                Q11_CALL_ARGUMENTS: [],
            },
        )
        entries = build_value_source_chain(runner, "val:order", 1, 3, 100)
        assert len(entries) == 1
        assert entries[0].member_ref.reference_type == "instantiation"


# =============================================================================
# Source chain with no data
# =============================================================================


class TestSourceChainNoData:
    """Tests for source chain edge cases."""

    def test_empty_source_data_returns_empty(self):
        runner = make_runner(execute_map={Q9_SOURCE_CHAIN: []})
        entries = build_value_source_chain(runner, "val:x", 1, 3, 100)
        assert entries == []

    def test_no_source_no_result_returns_empty(self):
        runner = make_runner(
            execute_map={
                Q9_SOURCE_CHAIN: [
                    {
                        "value_kind": "local",
                        "value_fqn": "A::$x",
                        "source_id": None, "source_kind": None,
                        "call_id": None, "call_file": None, "call_line": None,
                        "call_kind": None, "callee_id": None, "callee_fqn": None,
                        "callee_name": None, "callee_kind": None,
                        "callee_signature": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                    },
                ],
            },
        )
        entries = build_value_source_chain(runner, "val:x", 1, 3, 100)
        assert entries == []
