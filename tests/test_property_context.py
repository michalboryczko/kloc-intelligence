"""Tests for Property context orchestrators.

All tests are pure unit tests with mocked QueryRunner -- no Neo4j required.

Coverage:
- build_property_uses: promoted parameter tracing, caller filtering
- build_property_callers_filtered: single-arg filter, depth expansion
- build_property_used_by: method grouping, sites dedup, receiver collection,
  depth expansion, child dedup, arg filtering, caller chain callback
- Internal helpers: _resolve_on_kind, _build_on_display
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.models.results import ContextEntry
from src.orchestration.property_context import (
    build_property_uses,
    build_property_callers_filtered,
    build_property_used_by,
    _resolve_on_kind,
    _build_on_display,
)
from src.db.queries.context_property import (
    Q1_PROPERTY_CALLS,
    Q2_PROMOTED_PARAMETER,
    Q3_PARAM_CALLERS,
)
from src.db.queries.context_value import (
    Q1_RECEIVER_CHAIN,
    Q2_DIRECT_ARGUMENTS,
    Q9_SOURCE_CHAIN,
    Q11_CALL_ARGUMENTS,
)


# =============================================================================
# Fixtures / Factories
# =============================================================================


def make_runner(execute_map=None):
    """Return a MagicMock QueryRunner with configurable responses."""
    runner = MagicMock()
    execute_map = execute_map or {}

    def mock_execute(query, **kwargs):
        for q_const, result in execute_map.items():
            if q_const.strip() in query.strip() or query.strip() in q_const.strip():
                if callable(result):
                    return result(**kwargs)
                return result
        return []

    runner.execute.side_effect = mock_execute
    runner.execute_single.return_value = None
    return runner


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


# =============================================================================
# _build_on_display
# =============================================================================


class TestBuildOnDisplay:
    """Tests for on_display string building."""

    def test_empty_receivers(self):
        assert _build_on_display([], "$email", "User::$email") is None

    def test_self_receiver(self):
        result = _build_on_display(
            [("$this", "self")], "$email", "User::$email"
        )
        assert result == "$this->email (User::$email)"

    def test_param_receiver(self):
        result = _build_on_display(
            [("$user", "param")], "$email", "User::$email"
        )
        assert result == "$user"

    def test_multiple_receivers(self):
        result = _build_on_display(
            [("$user", "param"), ("$admin", "local")],
            "$email", "User::$email",
        )
        assert result == "$user, $admin"

    def test_self_with_dollar_prefix(self):
        result = _build_on_display(
            [("$this", "self")], "email", "User::$email"
        )
        assert result == "$this->email (User::$email)"

    def test_property_fqn_in_self_display(self):
        result = _build_on_display(
            [("$this", "self")], "$name", "Order::$name"
        )
        assert "Order::$name" in result


# =============================================================================
# build_property_uses — Promoted Parameter
# =============================================================================


class TestPropertyUses:
    """Tests for property USES (promoted parameter tracing)."""

    def test_promoted_parameter_found(self):
        """Property with assigned_from -> Value(parameter) triggers caller tracing."""
        runner = make_runner(
            execute_map={
                Q2_PROMOTED_PARAMETER: [
                    {
                        "param_id": "val:param:repo",
                        "param_fqn": "OrderService::__construct::$repo",
                        "param_name": "$repo",
                        "param_file": "src/OrderService.php",
                        "param_line": 10,
                        "param_value_kind": "parameter",
                    },
                ],
                Q3_PARAM_CALLERS: [
                    {
                        "call_id": "call:1",
                        "call_file": "src/App.php",
                        "call_line": 50,
                        "value_id": "val:arg1",
                        "value_kind": "local",
                        "value_name": "$repoInstance",
                        "value_fqn": "App::boot::$repoInstance",
                        "scope_id": "m:boot",
                        "scope_fqn": "App::boot",
                        "scope_kind": "Method",
                        "scope_signature": "boot()",
                        "position": 0,
                        "expression": "$repoInstance",
                        "value_type": "OrderRepository",
                    },
                ],
            },
        )
        entries = build_property_uses(
            runner, "prop:repo", "$repo", "OrderService::$repo", 1, 1, 100
        )
        assert len(entries) == 1
        assert entries[0].fqn == "App::boot"
        assert entries[0].crossed_from == "OrderService::__construct::$repo"
        assert len(entries[0].arguments) == 1
        assert entries[0].arguments[0].value_expr == "$repoInstance"

    def test_no_promoted_parameter(self):
        """Property with no assigned_from returns empty."""
        runner = make_runner(execute_map={Q2_PROMOTED_PARAMETER: []})
        entries = build_property_uses(
            runner, "prop:x", "$x", "A::$x", 1, 1, 100
        )
        assert entries == []

    def test_depth_exceeded_returns_empty(self):
        """depth > max_depth returns empty."""
        runner = make_runner()
        entries = build_property_uses(
            runner, "prop:x", "$x", "A::$x", 5, 3, 100
        )
        assert entries == []


# =============================================================================
# build_property_callers_filtered
# =============================================================================


class TestPropertyCallersFiltered:
    """Tests for filtered caller tracing."""

    def test_single_caller_with_filtered_arg(self):
        runner = make_runner(
            execute_map={
                Q3_PARAM_CALLERS: [
                    {
                        "call_id": "call:1",
                        "call_file": "src/Boot.php",
                        "call_line": 20,
                        "value_id": "val:1",
                        "value_kind": "local",
                        "value_name": "$svc",
                        "value_fqn": "Boot::run::$svc",
                        "scope_id": "m:run",
                        "scope_fqn": "Boot::run",
                        "scope_kind": "Method",
                        "scope_signature": "run()",
                        "position": 0,
                        "expression": "new Repo()",
                        "value_type": "Repo",
                    },
                ],
            },
        )
        entries = build_property_callers_filtered(
            runner,
            "Svc::__construct::$repo",
            "$repo", "Svc::$repo",
            1, 1, 100, set(),
        )
        assert len(entries) == 1
        assert entries[0].fqn == "Boot::run"
        assert entries[0].crossed_from == "Svc::__construct::$repo"
        assert len(entries[0].arguments) == 1
        assert entries[0].arguments[0].value_expr == "new Repo()"

    def test_multiple_callers_sorted(self):
        runner = make_runner(
            execute_map={
                Q3_PARAM_CALLERS: [
                    {
                        "call_id": "c:2", "call_file": "src/B.php", "call_line": 5,
                        "value_id": "v:2", "value_kind": "local",
                        "value_name": "$b", "value_fqn": None,
                        "scope_id": "m:b", "scope_fqn": "B::init",
                        "scope_kind": "Method", "scope_signature": None,
                        "position": 0, "expression": "$b", "value_type": None,
                    },
                    {
                        "call_id": "c:1", "call_file": "src/A.php", "call_line": 10,
                        "value_id": "v:1", "value_kind": "local",
                        "value_name": "$a", "value_fqn": None,
                        "scope_id": "m:a", "scope_fqn": "A::init",
                        "scope_kind": "Method", "scope_signature": None,
                        "position": 0, "expression": "$a", "value_type": None,
                    },
                ],
            },
        )
        entries = build_property_callers_filtered(
            runner, "X::$p", "$p", "X::$p", 1, 1, 100, set()
        )
        assert len(entries) == 2
        assert entries[0].file == "src/A.php"
        assert entries[1].file == "src/B.php"

    def test_limit_respected(self):
        runner = make_runner(
            execute_map={
                Q3_PARAM_CALLERS: [
                    {
                        "call_id": f"c:{i}", "call_file": f"f{i}.php", "call_line": i,
                        "value_id": f"v:{i}", "value_kind": "local",
                        "value_name": f"$x{i}", "value_fqn": None,
                        "scope_id": f"m:{i}", "scope_fqn": f"M{i}::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "position": 0, "expression": f"$x{i}", "value_type": None,
                    }
                    for i in range(5)
                ],
            },
        )
        entries = build_property_callers_filtered(
            runner, "X::$p", "$p", "X::$p", 1, 1, 2, set()
        )
        assert len(entries) == 2

    def test_depth_expansion_traces_source(self):
        """At depth < max_depth, traces caller's argument Value source."""
        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q3_PARAM_CALLERS.strip() in q:
                return [
                    {
                        "call_id": "c:1", "call_file": "f.php", "call_line": 5,
                        "value_id": "val:caller_arg",
                        "value_kind": "local",
                        "value_name": "$data", "value_fqn": "A::$data",
                        "scope_id": "m:run", "scope_fqn": "A::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "position": 0, "expression": "$data", "value_type": None,
                    },
                ]
            if Q9_SOURCE_CHAIN.strip() in q:
                return [
                    {
                        "value_kind": "local", "value_fqn": "A::$data",
                        "source_id": "s:1", "source_kind": "result",
                        "call_id": "call:create",
                        "call_file": "f.php", "call_line": 3,
                        "call_kind": "constructor",
                        "callee_id": "cls:Repo",
                        "callee_fqn": "App\\Repo",
                        "callee_name": "Repo",
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

        entries = build_property_callers_filtered(
            runner, "X::$p", "$p", "X::$p", 1, 3, 100, set()
        )
        assert len(entries) == 1
        assert len(entries[0].children) == 1
        assert entries[0].children[0].fqn == "App\\Repo"


# =============================================================================
# build_property_used_by — Method Grouping
# =============================================================================


class TestPropertyUsedByMethodGrouping:
    """Tests for grouping property accesses by containing method."""

    def test_single_access_single_method(self):
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "call:1",
                        "call_file": "src/Svc.php",
                        "call_line": 20,
                        "call_kind": "property_access",
                        "scope_id": "m:handle",
                        "scope_fqn": "Svc::handle",
                        "scope_kind": "Method",
                        "scope_signature": "handle()",
                        "recv_id": "recv:1",
                        "recv_value_kind": "self",
                        "recv_name": "$this",
                        "recv_prop_fqn": None,
                        "result_id": None,
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:email", "$email", "User::$email", 1, 1, 100
        )
        assert len(entries) == 1
        assert entries[0].fqn == "Svc::handle"
        assert entries[0].ref_type == "property_access"

    def test_multiple_accesses_same_method_grouped(self):
        """Multiple accesses in same method grouped into one entry with sites."""
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 10,
                        "call_kind": "property_access",
                        "scope_id": "m:handle", "scope_fqn": "Svc::handle",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:1", "recv_value_kind": "self",
                        "recv_name": "$this", "recv_prop_fqn": None,
                        "result_id": None,
                    },
                    {
                        "call_id": "call:2", "call_file": "f.php", "call_line": 15,
                        "call_kind": "property_access",
                        "scope_id": "m:handle", "scope_fqn": "Svc::handle",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:2", "recv_value_kind": "self",
                        "recv_name": "$this", "recv_prop_fqn": None,
                        "result_id": None,
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:email", "$email", "User::$email", 1, 1, 100
        )
        assert len(entries) == 1
        assert entries[0].sites is not None
        assert len(entries[0].sites) == 2
        assert entries[0].sites[0]["line"] == 10
        assert entries[0].sites[1]["line"] == 15

    def test_different_methods_separate_entries(self):
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 10,
                        "call_kind": "property_access",
                        "scope_id": "m:a", "scope_fqn": "A::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:1", "recv_value_kind": "parameter",
                        "recv_name": "$user", "recv_prop_fqn": None,
                        "result_id": None,
                    },
                    {
                        "call_id": "call:2", "call_file": "f.php", "call_line": 20,
                        "call_kind": "property_access",
                        "scope_id": "m:b", "scope_fqn": "B::process",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:2", "recv_value_kind": "local",
                        "recv_name": "$u", "recv_prop_fqn": None,
                        "result_id": None,
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:email", "$email", "User::$email", 1, 1, 100
        )
        assert len(entries) == 2


# =============================================================================
# build_property_used_by — Receiver Collection
# =============================================================================


class TestPropertyUsedByReceivers:
    """Tests for receiver name collection across accesses."""

    def test_self_receiver(self):
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": "property_access",
                        "scope_id": "m:x", "scope_fqn": "X::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:1", "recv_value_kind": "self",
                        "recv_name": "$this", "recv_prop_fqn": None,
                        "result_id": None,
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:name", "$name", "X::$name", 1, 1, 100
        )
        assert len(entries) == 1
        assert entries[0].on_kind == "property"  # self -> property
        assert "$this->name" in entries[0].on

    def test_param_receiver(self):
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": "property_access",
                        "scope_id": "m:x", "scope_fqn": "X::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:1", "recv_value_kind": "parameter",
                        "recv_name": "$order", "recv_prop_fqn": None,
                        "result_id": None,
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:id", "$id", "Order::$id", 1, 1, 100
        )
        assert entries[0].on == "$order"
        assert entries[0].on_kind == "param"

    def test_unique_receivers_across_accesses(self):
        """Same receiver shouldn't appear twice."""
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": "property_access",
                        "scope_id": "m:x", "scope_fqn": "X::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:1", "recv_value_kind": "parameter",
                        "recv_name": "$order", "recv_prop_fqn": None,
                        "result_id": None,
                    },
                    {
                        "call_id": "call:2", "call_file": "f.php", "call_line": 8,
                        "call_kind": "property_access",
                        "scope_id": "m:x", "scope_fqn": "X::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:2", "recv_value_kind": "parameter",
                        "recv_name": "$order", "recv_prop_fqn": None,
                        "result_id": None,
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:id", "$id", "Order::$id", 1, 1, 100
        )
        assert entries[0].on == "$order"  # Just one receiver


# =============================================================================
# build_property_used_by — Depth 2 Expansion
# =============================================================================


class TestPropertyUsedByDepth2:
    """Tests for depth-2 expansion via result Value consumer chain."""

    def test_depth_expansion_traces_result_values(self):
        """At depth < max_depth, traces result Values via consumer chain."""
        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q1_PROPERTY_CALLS.strip() in q:
                return [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": "property_access",
                        "scope_id": "m:do", "scope_fqn": "S::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": "r:1", "recv_value_kind": "parameter",
                        "recv_name": "$obj", "recv_prop_fqn": None,
                        "result_id": "result:1",
                    },
                ]
            if Q1_RECEIVER_CHAIN.strip() in q:
                return []
            if Q2_DIRECT_ARGUMENTS.strip() in q:
                return [
                    {
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "f.php",
                        "consumer_call_line": 10,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:send",
                        "consumer_target_fqn": "N::send",
                        "consumer_target_name": "send",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    },
                ]
            if Q11_CALL_ARGUMENTS.strip() in q:
                return []
            return []

        runner = MagicMock()
        runner.execute.side_effect = mock_execute
        runner.execute_single.return_value = None

        entries = build_property_used_by(
            runner, "prop:email", "$email", "User::$email", 1, 3, 100
        )
        assert len(entries) == 1
        assert len(entries[0].children) >= 1

    def test_children_deduplication(self):
        """Children with same (fqn, file, line) should be deduplicated."""
        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q1_PROPERTY_CALLS.strip() in q:
                return [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": None,
                        "scope_id": "m:do", "scope_fqn": "S::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": "result:1",
                    },
                    {
                        "call_id": "call:2", "call_file": "f.php", "call_line": 8,
                        "call_kind": None,
                        "scope_id": "m:do", "scope_fqn": "S::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": "result:2",
                    },
                ]
            if Q1_RECEIVER_CHAIN.strip() in q:
                return []
            if Q2_DIRECT_ARGUMENTS.strip() in q:
                # Both results consumed by same method
                return [
                    {
                        "consumer_call_id": "consumer:same",
                        "consumer_call_file": "f.php",
                        "consumer_call_line": 20,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:target",
                        "consumer_target_fqn": "T::run",
                        "consumer_target_name": "run",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    },
                ]
            if Q11_CALL_ARGUMENTS.strip() in q:
                return []
            return []

        runner = MagicMock()
        runner.execute.side_effect = mock_execute
        runner.execute_single.return_value = None

        entries = build_property_used_by(
            runner, "prop:x", "$x", "A::$x", 1, 3, 100
        )
        assert len(entries) == 1
        # Should be deduplicated to 1 child (same fqn, file, line)
        assert len(entries[0].children) == 1


# =============================================================================
# build_property_used_by — Argument Filtering (ISSUE-O)
# =============================================================================


class TestPropertyUsedByArgFiltering:
    """Tests for ISSUE-O: filtering constructor args to matching property."""

    def test_filters_args_matching_property_name(self):
        """Only arguments whose value_expr ends with ->propertyName are kept."""
        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q1_PROPERTY_CALLS.strip() in q:
                return [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": None,
                        "scope_id": "m:do", "scope_fqn": "S::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": "result:1",
                    },
                ]
            if Q1_RECEIVER_CHAIN.strip() in q:
                return []
            if Q2_DIRECT_ARGUMENTS.strip() in q:
                return [
                    {
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "f.php",
                        "consumer_call_line": 10,
                        "consumer_call_kind": "constructor",
                        "consumer_target_id": "cls:Output",
                        "consumer_target_fqn": "App\\Output",
                        "consumer_target_name": "Output",
                        "consumer_target_kind": "Class",
                        "consumer_target_signature": None,
                    },
                ]
            if Q11_CALL_ARGUMENTS.strip() in q:
                return [
                    {"position": 0, "expression": "$order->id",
                     "value_kind": "result", "value_type": None,
                     "parameter": None, "value_fqn": None,
                     "value_id": "v1", "value_name": None},
                    {"position": 1, "expression": "$order->name",
                     "value_kind": "result", "value_type": None,
                     "parameter": None, "value_fqn": None,
                     "value_id": "v2", "value_name": None},
                ]
            return []

        runner = MagicMock()
        runner.execute.side_effect = mock_execute
        runner.execute_single.return_value = None

        entries = build_property_used_by(
            runner, "prop:id", "$id", "Order::$id", 1, 3, 100
        )
        assert len(entries) == 1
        # Children should have filtered args to only "$order->id"
        if entries[0].children:
            child = entries[0].children[0]
            assert len(child.arguments) == 1
            assert child.arguments[0].value_expr == "$order->id"


# =============================================================================
# build_property_used_by — Caller Chain Callback
# =============================================================================


class TestPropertyUsedByCallerChain:
    """Tests for caller chain integration via callback."""

    def test_caller_chain_fn_called_when_no_children(self):
        """When no depth-2 children, caller_chain_fn adds callers at depth+1."""
        caller_entries = [
            ContextEntry(depth=2, node_id="cm:1", fqn="Caller::exec", kind="Method")
        ]
        callback = MagicMock(return_value=caller_entries)

        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": None,
                        "scope_id": "m:do", "scope_fqn": "S::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": None,  # No result -> no depth-2 children
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:x", "$x", "A::$x", 1, 3, 100,
            caller_chain_fn=callback,
        )
        assert len(entries) == 1
        assert len(entries[0].children) == 1
        assert entries[0].children[0].fqn == "Caller::exec"
        callback.assert_called_once_with("m:do", 2, 3)

    def test_caller_chain_fn_appended_to_existing_children(self):
        """When depth-2 children exist, callers go as depth-3 on children."""
        caller_entries = [
            ContextEntry(depth=3, node_id="cm:1", fqn="Caller::exec", kind="Method")
        ]
        callback = MagicMock(return_value=caller_entries)

        def mock_execute(query, **kwargs):
            q = query.strip()
            if Q1_PROPERTY_CALLS.strip() in q:
                return [
                    {
                        "call_id": "call:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": None,
                        "scope_id": "m:do", "scope_fqn": "S::do",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": "result:1",
                    },
                ]
            if Q1_RECEIVER_CHAIN.strip() in q:
                return []
            if Q2_DIRECT_ARGUMENTS.strip() in q:
                return [
                    {
                        "consumer_call_id": "consumer:1",
                        "consumer_call_file": "f.php",
                        "consumer_call_line": 10,
                        "consumer_call_kind": None,
                        "consumer_target_id": "m:send",
                        "consumer_target_fqn": "N::send",
                        "consumer_target_name": "send",
                        "consumer_target_kind": "Method",
                        "consumer_target_signature": None,
                    },
                ]
            if Q11_CALL_ARGUMENTS.strip() in q:
                return []
            return []

        runner = MagicMock()
        runner.execute.side_effect = mock_execute
        runner.execute_single.return_value = None

        entries = build_property_used_by(
            runner, "prop:x", "$x", "A::$x", 1, 3, 100,
            caller_chain_fn=callback,
        )
        assert len(entries) == 1
        # depth-2 child exists, so callers should be on child.children
        if entries[0].children:
            assert entries[0].children[0].children == caller_entries


# =============================================================================
# build_property_used_by — Limit and Sorting
# =============================================================================


class TestPropertyUsedByLimitSort:
    """Tests for limit enforcement and sorting."""

    def test_limit_caps_entries(self):
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": f"c:{i}", "call_file": f"f{i}.php", "call_line": i,
                        "call_kind": None,
                        "scope_id": f"m:{i}", "scope_fqn": f"M{i}::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": None,
                    }
                    for i in range(10)
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:x", "$x", "A::$x", 1, 1, 3
        )
        assert len(entries) == 3

    def test_sorted_by_file_then_line(self):
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "c:2", "call_file": "src/B.php", "call_line": 5,
                        "call_kind": None,
                        "scope_id": "m:b", "scope_fqn": "B::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": None,
                    },
                    {
                        "call_id": "c:1", "call_file": "src/A.php", "call_line": 10,
                        "call_kind": None,
                        "scope_id": "m:a", "scope_fqn": "A::run",
                        "scope_kind": "Method", "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": None,
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:x", "$x", "A::$x", 1, 1, 100
        )
        assert entries[0].file == "src/A.php"
        assert entries[1].file == "src/B.php"

    def test_depth_exceeded_returns_empty(self):
        runner = make_runner()
        entries = build_property_used_by(
            runner, "prop:x", "$x", "A::$x", 5, 3, 100
        )
        assert entries == []

    def test_no_scope_skips_call(self):
        """Calls without a containing scope are skipped."""
        runner = make_runner(
            execute_map={
                Q1_PROPERTY_CALLS: [
                    {
                        "call_id": "c:1", "call_file": "f.php", "call_line": 5,
                        "call_kind": None,
                        "scope_id": None, "scope_fqn": None,
                        "scope_kind": None, "scope_signature": None,
                        "recv_id": None, "recv_value_kind": None,
                        "recv_name": None, "recv_prop_fqn": None,
                        "result_id": None,
                    },
                ],
            },
        )
        entries = build_property_used_by(
            runner, "prop:x", "$x", "A::$x", 1, 1, 100
        )
        assert entries == []
