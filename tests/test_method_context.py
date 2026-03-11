"""Tests for Method context orchestrators and polymorphic support.

All tests are pure unit tests with mocked QueryRunner -- no Neo4j required.

Coverage:
- build_execution_flow: Kind 1 (local_variable + source_call), Kind 2 (call)
- Consumed call detection and exclusion
- External calls (no callee)
- Cycle prevention
- Depth expansion
- filter_orphan_property_accesses
- get_type_references
- Receiver identity resolution (on, on_kind)
- ArgumentInfo building
- build_method_used_by
- Polymorphic: get_implementations_for_node
- Polymorphic: get_interface_method_ids
- Polymorphic: get_concrete_implementors
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.models.node import NodeData
from src.models.results import ContextEntry, MemberRef, ArgumentInfo
from src.orchestration.method_context import (
    build_execution_flow,
    build_method_used_by,
    filter_orphan_property_accesses,
    get_type_references,
    _resolve_receiver_identity,
    _build_member_ref,
    _build_argument_infos,
)
from src.logic.polymorphic import (
    get_implementations_for_node,
    get_interface_method_ids,
    get_concrete_implementors,
)


# =============================================================================
# Fixtures / Factories
# =============================================================================


def make_method_node(**overrides) -> NodeData:
    """Return a NodeData for a Method with sensible defaults."""
    defaults = {
        "node_id": "m:doWork",
        "kind": "Method",
        "name": "doWork",
        "fqn": "App\\Service\\OrderService::doWork",
        "symbol": "scip-php . `App\\Service\\OrderService::doWork`.",
        "file": "src/Service/OrderService.php",
        "start_line": 25,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def make_class_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "cls:Order",
        "kind": "Class",
        "name": "Order",
        "fqn": "App\\Entity\\Order",
        "symbol": "scip-php . `App\\Entity\\Order`.",
        "file": "src/Entity/Order.php",
        "start_line": 10,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def make_runner() -> MagicMock:
    runner = MagicMock()
    runner.execute.return_value = []
    return runner


def make_execution_data_runner(
    calls=None,
    arguments=None,
    consumed_ids=None,
) -> MagicMock:
    """Build a runner that returns fixed data for fetch_method_execution_data queries."""
    from src.db.queries.context_method import (
        Q1_METHOD_CALLS,
        Q2_CALL_ARGUMENTS,
        Q3_CONSUMED_CALLS,
    )

    runner = MagicMock()

    def side_effect(query, **kwargs):
        q = query.strip()
        if Q1_METHOD_CALLS.strip() in q or q in Q1_METHOD_CALLS.strip():
            return calls or []
        if Q2_CALL_ARGUMENTS.strip() in q or q in Q2_CALL_ARGUMENTS.strip():
            return arguments or []
        if Q3_CONSUMED_CALLS.strip() in q or q in Q3_CONSUMED_CALLS.strip():
            consumed = consumed_ids or []
            return [{"consumed_call_id": cid} for cid in consumed]
        return []

    runner.execute.side_effect = side_effect
    return runner


# =============================================================================
# _resolve_receiver_identity
# =============================================================================


class TestResolveReceiverIdentity:
    """Tests for receiver identity resolution.

    _resolve_receiver_identity returns a 5-tuple:
    (access_chain, access_chain_symbol, on_kind, on_file, on_line)
    """

    def test_none_recv_kind_returns_none(self):
        ac, acs, ok, of, ol = _resolve_receiver_identity(None, None, None)
        assert ac is None
        assert ok is None
        assert of is None
        assert ol is None

    def test_parameter_recv(self):
        ac, acs, ok, of, ol = _resolve_receiver_identity("parameter", "$service", None)
        assert ac == "$service"
        assert ok == "param"

    def test_local_recv(self):
        ac, acs, ok, of, ol = _resolve_receiver_identity("local", "$repo", None)
        assert ac == "$repo"
        assert ok == "local"

    def test_self_recv(self):
        ac, acs, ok, of, ol = _resolve_receiver_identity("self", None, None)
        assert ac == "$this"
        assert ok == "self"

    def test_result_recv_without_runner_returns_result_kind(self):
        ac, acs, ok, of, ol = _resolve_receiver_identity("result", "result_val", "call:1")
        assert ok == "result"

    def test_result_recv_with_runner_resolves_property(self):
        runner = MagicMock()
        runner.execute.return_value = [
            {"prop_fqn": "App\\Svc::$repo", "prop_name": "repo", "recv_kind": "result",
             "recv_name": None, "src_call_kind": "property_access",
             "src_recv_kind": "parameter", "src_recv_name": "$this"}
        ]
        ac, acs, ok, of, ol = _resolve_receiver_identity("result", None, "call:1", runner)
        assert ac == "$this->repo"
        assert ok == "property"

    def test_result_recv_with_runner_no_prop(self):
        runner = MagicMock()
        runner.execute.return_value = [
            {"prop_fqn": None, "prop_name": None, "recv_kind": "result",
             "recv_name": "rv", "src_call_kind": None,
             "src_recv_kind": None, "src_recv_name": None}
        ]
        ac, acs, ok, of, ol = _resolve_receiver_identity("result", "rv", "call:1", runner)
        # No prop_fqn found; falls through to result
        assert ok == "result"

    def test_unknown_recv_kind_passthrough(self):
        ac, acs, ok, of, ol = _resolve_receiver_identity("static", "$this", None)
        assert ac == "$this"
        assert ok == "static"

    def test_parameter_recv_with_file_line(self):
        ac, acs, ok, of, ol = _resolve_receiver_identity(
            "parameter", "$service", None, None,
            "src/Service.php", 10
        )
        assert ac == "$service"
        assert ok == "param"
        assert of == "src/Service.php"
        assert ol == 10


# =============================================================================
# _build_member_ref
# =============================================================================


class TestBuildMemberRef:
    """Tests for MemberRef construction.

    _build_member_ref(callee_fqn, callee_name, callee_kind,
        access_chain, access_chain_symbol, on_kind,
        call_kind, call_file, call_line, on_file, on_line)
    """

    def test_returns_none_when_no_callee_data(self):
        ref = _build_member_ref(None, None, None, None, None, None)
        assert ref is None

    def test_method_callee_adds_parens(self):
        ref = _build_member_ref("App\\Repo::save", "save", "Method", None, None, None)
        assert ref is not None
        assert ref.target_name == "save()"

    def test_property_callee_no_parens(self):
        ref = _build_member_ref(
            "App\\Entity::$name", "name", "Property",
            "$this", None, "self",
            call_kind="access",
        )
        assert ref is not None
        assert ref.target_name == "name"
        assert ref.reference_type == "property_access"
        assert ref.access_chain == "$this"
        assert ref.on_kind == "self"

    def test_access_chain_from_on(self):
        ref = _build_member_ref(
            "App\\Repo::save", "save", "Method",
            "App\\Svc::$repo", None, "property",
        )
        assert ref.access_chain == "App\\Svc::$repo"

    def test_access_chain_fallback_to_recv_name(self):
        ref = _build_member_ref(
            "App\\Repo::save", "save", "Method",
            "$this->repo", None, "property",
        )
        assert ref.access_chain == "$this->repo"

    def test_call_kind_maps_to_reference_type(self):
        ref = _build_member_ref(
            "App\\Entity", "__construct", "Method",
            None, None, None,
            call_kind="constructor",
        )
        assert ref.reference_type == "instantiation"


# =============================================================================
# _build_argument_infos
# =============================================================================


class TestBuildArgumentInfos:
    """Tests for ArgumentInfo list construction."""

    def test_empty_returns_empty(self):
        infos = _build_argument_infos("call:1", {})
        assert infos == []

    def test_single_argument(self):
        args_by_call = {
            "call:1": [{"position": 0, "expression": "$id", "value_kind": "local",
                         "value_type": "int", "value_fqn": None}]
        }
        infos = _build_argument_infos("call:1", args_by_call)
        assert len(infos) == 1
        assert infos[0].position == 0
        assert infos[0].value_expr == "$id"
        assert infos[0].value_source == "local"
        assert infos[0].value_type == "int"

    def test_multiple_arguments_sorted_by_position(self):
        args_by_call = {
            "call:1": [
                {"position": 1, "expression": "$b", "value_kind": "local", "value_type": None, "value_fqn": None},
                {"position": 0, "expression": "$a", "value_kind": "local", "value_type": None, "value_fqn": None},
            ]
        }
        infos = _build_argument_infos("call:1", args_by_call)
        assert infos[0].position == 0
        assert infos[0].value_expr == "$a"
        assert infos[1].position == 1
        assert infos[1].value_expr == "$b"

    def test_skips_records_without_position(self):
        args_by_call = {
            "call:1": [{"position": None, "expression": "$x", "value_kind": "local",
                         "value_type": None, "value_fqn": None}]
        }
        infos = _build_argument_infos("call:1", args_by_call)
        assert infos == []


# =============================================================================
# filter_orphan_property_accesses
# =============================================================================


class TestFilterOrphanPropertyAccesses:
    """Tests for orphan property access filtering."""

    def _make_call_entry(self, ref_type: str, access_chain: str | None = None, **kwargs) -> ContextEntry:
        member_ref = MemberRef(
            target_name="prop",
            target_fqn="App\\Entity::$prop",
            reference_type=ref_type,
            access_chain=access_chain,
        )
        return ContextEntry(
            depth=1,
            node_id="m:1",
            fqn="App\\Svc::doWork",
            entry_type="call",
            member_ref=member_ref,
            **kwargs,
        )

    def test_empty_list_returns_empty(self):
        assert filter_orphan_property_accesses([]) == []

    def test_non_property_access_not_filtered(self):
        entry = self._make_call_entry("method_call", "$this->prop")
        result = filter_orphan_property_accesses([entry])
        assert len(result) == 1

    def test_property_access_not_orphan_kept(self):
        """Property access entry whose access_chain is not in any argument."""
        entry = self._make_call_entry("property_access", "App\\Entity::$status")
        result = filter_orphan_property_accesses([entry])
        assert len(result) == 1

    def test_property_access_orphan_filtered(self):
        """Property access entry whose expression appears in an argument -> filtered."""
        prop_entry = ContextEntry(
            depth=1,
            node_id="m:1",
            fqn="App\\Entity\\Order::$status",
            entry_type="call",
            member_ref=MemberRef(
                target_name="$status",
                target_fqn="App\\Entity\\Order::$status",
                reference_type="property_access",
                access_chain="$order",
            ),
        )

        method_entry = ContextEntry(
            depth=1,
            node_id="m:2",
            fqn="App\\Svc::save()",
            entry_type="call",
            arguments=[
                ArgumentInfo(position=0, value_expr="$order->status")
            ],
        )
        result = filter_orphan_property_accesses([prop_entry, method_entry])
        # prop_entry filtered because "$order->status" is in argument value_exprs
        assert len(result) == 1
        assert result[0].fqn == "App\\Svc::save()"

    def test_non_call_entry_never_filtered(self):
        """local_variable entries are never filtered."""
        entry = ContextEntry(
            depth=1,
            node_id="v:1",
            fqn="$result",
            entry_type="local_variable",
            member_ref=MemberRef(
                target_name="prop",
                target_fqn="App\\Entity::$prop",
                reference_type="property_access",
                access_chain="App\\Entity::$prop",
            ),
        )
        arg_entry = ContextEntry(
            depth=1,
            node_id="m:1",
            fqn="App\\Svc::save",
            entry_type="call",
            arguments=[ArgumentInfo(position=0, value_expr="App\\Entity::$prop")],
        )
        result = filter_orphan_property_accesses([entry, arg_entry])
        assert len(result) == 2  # local_variable kept


# =============================================================================
# get_type_references
# =============================================================================


class TestGetTypeReferences:
    """Tests for Q4 type reference extraction.

    get_type_references now:
    - Skips parameter_type and return_type (only keeps property_type/type_hint)
    - Skips targets covered by constructor calls
    - Creates member_ref instead of setting ref_type directly
    """

    def _make_type_ref_runner(self, q4_records, ctor_records=None):
        """Build a runner that handles both Q4 and constructor target queries."""
        runner = MagicMock()

        def side_effect(query, **kwargs):
            q = query.strip()
            if "constructor" in q:
                return ctor_records or []
            return q4_records

        runner.execute.side_effect = side_effect
        return runner

    def test_empty_returns_empty(self):
        runner = self._make_type_ref_runner([])
        result = get_type_references(runner, "m:doWork")
        assert result == []

    def test_return_type_is_skipped(self):
        """return_type entries are excluded (shown in DEFINITION section)."""
        runner = self._make_type_ref_runner([{
            "target_id": "cls:Result",
            "target_fqn": "App\\Result",
            "target_kind": "Class",
            "target_signature": None,
            "target_file": "src/Result.php",
            "target_start_line": 5,
            "file": "src/Service.php",
            "line": 30,
            "has_arg_th": False,
            "has_ret_th": True,
        }])
        result = get_type_references(runner, "m:doWork")
        # return_type is excluded
        assert len(result) == 0

    def test_parameter_type_wins_over_return_type(self):
        """parameter_type is excluded (shown in DEFINITION section)."""
        runner = self._make_type_ref_runner([{
            "target_id": "cls:Entity",
            "target_fqn": "App\\Entity",
            "target_kind": "Class",
            "target_signature": None,
            "target_file": None,
            "target_start_line": None,
            "file": None,
            "line": None,
            "has_arg_th": True,
            "has_ret_th": True,
        }])
        result = get_type_references(runner, "m:doWork")
        # parameter_type is excluded
        assert len(result) == 0

    def test_type_hint_fallback(self):
        """type_hint entries are included with a member_ref."""
        runner = self._make_type_ref_runner([{
            "target_id": "cls:Dep",
            "target_fqn": "App\\Dep",
            "target_kind": "Interface",
            "target_signature": None,
            "target_file": None,
            "target_start_line": None,
            "file": None,
            "line": None,
            "has_arg_th": False,
            "has_ret_th": False,
        }])
        result = get_type_references(runner, "m:doWork")
        assert len(result) == 1
        assert result[0].ref_type is None  # No top-level ref_type
        assert result[0].member_ref is not None
        assert result[0].member_ref.reference_type == "type_hint"
        assert result[0].member_ref.target_fqn == "App\\Dep"

    def test_deduplication_by_target_id(self):
        runner = self._make_type_ref_runner([
            {"target_id": "cls:X", "target_fqn": "App\\X", "target_kind": "Class",
             "target_signature": None, "target_file": None, "target_start_line": None,
             "file": None, "line": None, "has_arg_th": False, "has_ret_th": False},
            {"target_id": "cls:X", "target_fqn": "App\\X", "target_kind": "Class",
             "target_signature": None, "target_file": None, "target_start_line": None,
             "file": None, "line": None, "has_arg_th": False, "has_ret_th": False},
        ])
        result = get_type_references(runner, "m:doWork")
        # Only one entry for cls:X (dedup)
        assert len(result) == 1

    def test_limit_via_count(self):
        runner = self._make_type_ref_runner([
            {"target_id": f"cls:{i}", "target_fqn": f"App\\X{i}", "target_kind": "Class",
             "target_signature": None, "target_file": None, "target_start_line": None,
             "file": None, "line": None, "has_arg_th": False, "has_ret_th": False}
            for i in range(10)
        ])
        count = [0]
        result = get_type_references(runner, "m:doWork", count=count, limit=3)
        assert len(result) == 3
        assert count[0] == 3


# =============================================================================
# build_execution_flow
# =============================================================================


class TestBuildExecutionFlow:
    """Tests for the core method execution flow builder."""

    def test_empty_method_returns_empty(self):
        runner = make_execution_data_runner()
        result = build_execution_flow(runner, "m:doWork")
        assert result == []

    def test_kind2_standalone_call(self):
        """A call with no local_id is a Kind 2 standalone call entry."""
        runner = make_execution_data_runner(
            calls=[{
                "call_id": "call:1",
                "call_kind": "method",
                "call_name": "save",
                "call_file": "src/Service.php",
                "call_line": 30,
                "callee_id": "m:save",
                "callee_fqn": "App\\Repo::save",
                "callee_kind": "Method",
                "callee_name": "save",
                "callee_signature": "save(int $id): void",
                "callee_file": "src/Repo.php",
                "callee_start_line": 10,
                "recv_id": None,
                "recv_value_kind": None,
                "recv_name": None,
                "recv_source_call_id": None,
                "result_id": None,
                "local_id": None,
                "local_fqn": None,
                "local_name": None,
                "local_line": None,
                "local_type_name": None,
            }]
        )
        result = build_execution_flow(runner, "m:doWork")
        assert len(result) == 1
        entry = result[0]
        assert entry.entry_type == "call"
        assert entry.node_id == "m:save"
        assert entry.fqn == "App\\Repo::save()"
        assert entry.line == 30

    def test_kind1_local_variable(self):
        """A call with local_id produces a Kind 1 local_variable entry."""
        runner = make_execution_data_runner(
            calls=[{
                "call_id": "call:1",
                "call_kind": "method",
                "call_name": "find",
                "call_file": "src/Service.php",
                "call_line": 20,
                "callee_id": "m:find",
                "callee_fqn": "App\\Repo::find",
                "callee_kind": "Method",
                "callee_name": "find",
                "callee_signature": "find(int $id): Entity",
                "callee_file": "src/Repo.php",
                "callee_start_line": 5,
                "recv_id": None,
                "recv_value_kind": "parameter",
                "recv_name": "$this",
                "recv_source_call_id": None,
                "result_id": "val:result",
                "local_id": "val:order",
                "local_fqn": "App\\Service::doWork::$order",
                "local_name": "$order",
                "local_line": 20,
                "local_type_name": "Order",
            }]
        )
        result = build_execution_flow(runner, "m:doWork")
        assert len(result) == 1
        entry = result[0]
        assert entry.entry_type == "local_variable"
        assert entry.variable_name == "$order"
        assert entry.variable_type == "Order"
        # source_call is a nested ContextEntry
        assert entry.source_call is not None
        assert isinstance(entry.source_call, ContextEntry)
        assert entry.source_call.entry_type == "call"
        assert entry.source_call.fqn == "App\\Repo::find()"

    def test_consumed_call_excluded(self):
        """Calls consumed as receiver/argument of another call are excluded."""
        runner = make_execution_data_runner(
            calls=[
                {
                    "call_id": "call:1",
                    "call_kind": "method",
                    "call_name": "getRepo",
                    "call_file": "src/S.php",
                    "call_line": 10,
                    "callee_id": "m:getRepo",
                    "callee_fqn": "App\\Svc::getRepo",
                    "callee_kind": "Method",
                    "callee_name": "getRepo",
                    "callee_signature": None,
                    "callee_file": None,
                    "callee_start_line": None,
                    "recv_id": None,
                    "recv_value_kind": None,
                    "recv_name": None,
                    "recv_source_call_id": None,
                    "result_id": None,
                    "local_id": None,
                    "local_fqn": None,
                    "local_name": None,
                    "local_line": None,
                    "local_type_name": None,
                },
                {
                    "call_id": "call:2",
                    "call_kind": "method",
                    "call_name": "save",
                    "call_file": "src/S.php",
                    "call_line": 11,
                    "callee_id": "m:save",
                    "callee_fqn": "App\\Repo::save",
                    "callee_kind": "Method",
                    "callee_name": "save",
                    "callee_signature": None,
                    "callee_file": None,
                    "callee_start_line": None,
                    "recv_id": "val:recv",
                    "recv_value_kind": "result",
                    "recv_name": None,
                    "recv_source_call_id": "call:1",
                    "result_id": None,
                    "local_id": None,
                    "local_fqn": None,
                    "local_name": None,
                    "local_line": None,
                    "local_type_name": None,
                },
            ],
            consumed_ids=["call:1"],  # call:1 is consumed as receiver of call:2
        )
        result = build_execution_flow(runner, "m:doWork")
        # call:1 is consumed, only call:2 remains
        assert len(result) == 1
        assert result[0].node_id == "m:save"

    def test_external_call_no_callee_id(self):
        """External calls (callee_id=None) produce entries without recursion."""
        runner = make_execution_data_runner(
            calls=[{
                "call_id": "call:ext",
                "call_kind": "function",
                "call_name": "array_map",
                "call_file": "src/S.php",
                "call_line": 15,
                "callee_id": None,
                "callee_fqn": None,
                "callee_kind": None,
                "callee_name": "array_map",
                "callee_signature": None,
                "callee_file": None,
                "callee_start_line": None,
                "recv_id": None,
                "recv_value_kind": None,
                "recv_name": None,
                "recv_source_call_id": None,
                "result_id": None,
                "local_id": None,
                "local_fqn": None,
                "local_name": None,
                "local_line": None,
                "local_type_name": None,
            }]
        )
        result = build_execution_flow(runner, "m:doWork")
        assert len(result) == 1
        entry = result[0]
        assert entry.entry_type == "call"
        assert entry.node_id == "call:ext"  # falls back to call_id

    def test_cycle_guard_prevents_infinite_recursion(self):
        """cycle_guard prevents visiting the same callee twice."""
        runner = make_execution_data_runner(
            calls=[{
                "call_id": "call:1",
                "call_kind": "method",
                "call_name": "process",
                "call_file": None,
                "call_line": 5,
                "callee_id": "m:process",
                "callee_fqn": "App\\Svc::process",
                "callee_kind": "Method",
                "callee_name": "process",
                "callee_signature": None,
                "callee_file": None,
                "callee_start_line": None,
                "recv_id": None,
                "recv_value_kind": None,
                "recv_name": None,
                "recv_source_call_id": None,
                "result_id": None,
                "local_id": None,
                "local_fqn": None,
                "local_name": None,
                "local_line": None,
                "local_type_name": None,
            }]
        )
        # Add callee to cycle guard upfront
        cycle_guard = {"m:process"}
        result = build_execution_flow(runner, "m:doWork", cycle_guard=cycle_guard)
        # Entry is skipped due to cycle guard
        assert result == []

    def test_depth_limit_prevents_recursion(self):
        """Recursion stops when depth > max_depth."""
        # We use a fresh runner for the nested call too
        runner = make_execution_data_runner(
            calls=[{
                "call_id": "call:1",
                "call_kind": "method",
                "call_name": "helper",
                "call_file": None,
                "call_line": 5,
                "callee_id": "m:helper",
                "callee_fqn": "App\\Svc::helper",
                "callee_kind": "Method",
                "callee_name": "helper",
                "callee_signature": None,
                "callee_file": None,
                "callee_start_line": None,
                "recv_id": None,
                "recv_value_kind": None,
                "recv_name": None,
                "recv_source_call_id": None,
                "result_id": None,
                "local_id": None,
                "local_fqn": None,
                "local_name": None,
                "local_line": None,
                "local_type_name": None,
            }]
        )
        # max_depth=1 means no recursion
        result = build_execution_flow(runner, "m:doWork", max_depth=1)
        assert len(result) == 1
        # No children because depth would be 2 > max_depth 1
        assert result[0].children == []

    def test_receiver_identity_param_on_kind(self):
        """Method called on a parameter has member_ref.on_kind='param'."""
        runner = make_execution_data_runner(
            calls=[{
                "call_id": "call:1",
                "call_kind": "method",
                "call_name": "save",
                "call_file": None,
                "call_line": 5,
                "callee_id": "m:save",
                "callee_fqn": "App\\Repo::save",
                "callee_kind": "Method",
                "callee_name": "save",
                "callee_signature": None,
                "callee_file": None,
                "callee_start_line": None,
                "recv_id": "v:repo",
                "recv_value_kind": "parameter",
                "recv_name": "$repo",
                "recv_file": "src/Service.php",
                "recv_start_line": 25,
                "recv_source_call_id": None,
                "result_id": None,
                "local_id": None,
                "local_fqn": None,
                "local_name": None,
                "local_line": None,
                "local_type_name": None,
            }]
        )
        result = build_execution_flow(runner, "m:doWork")
        assert len(result) == 1
        assert result[0].member_ref is not None
        assert result[0].member_ref.access_chain == "$repo"
        assert result[0].member_ref.on_kind == "param"
        assert result[0].member_ref.on_file == "src/Service.php"
        assert result[0].member_ref.on_line == 25

    def test_arguments_populated_on_call_entry(self):
        runner = make_execution_data_runner(
            calls=[{
                "call_id": "call:1",
                "call_kind": "method",
                "call_name": "find",
                "call_file": None,
                "call_line": 5,
                "callee_id": "m:find",
                "callee_fqn": "App\\Repo::find",
                "callee_kind": "Method",
                "callee_name": "find",
                "callee_signature": None,
                "callee_file": None,
                "callee_start_line": None,
                "recv_id": None,
                "recv_value_kind": None,
                "recv_name": None,
                "recv_source_call_id": None,
                "result_id": None,
                "local_id": None,
                "local_fqn": None,
                "local_name": None,
                "local_line": None,
                "local_type_name": None,
            }],
            arguments=[{
                "call_id": "call:1",
                "position": 0,
                "expression": "$orderId",
                "value_kind": "local",
                "value_name": "$orderId",
                "value_fqn": None,
                "value_type": "int",
            }]
        )
        result = build_execution_flow(runner, "m:doWork")
        assert len(result) == 1
        assert len(result[0].arguments) == 1
        assert result[0].arguments[0].value_expr == "$orderId"
        assert result[0].arguments[0].value_type == "int"

    def test_sort_by_call_line(self):
        """Entries are sorted by call line number."""
        runner = make_execution_data_runner(
            calls=[
                {
                    "call_id": "call:B",
                    "call_kind": "method",
                    "call_name": "B",
                    "call_file": None,
                    "call_line": 20,
                    "callee_id": "m:B",
                    "callee_fqn": "App\\Svc::B",
                    "callee_kind": "Method",
                    "callee_name": "B",
                    "callee_signature": None,
                    "callee_file": None,
                    "callee_start_line": None,
                    "recv_id": None,
                    "recv_value_kind": None,
                    "recv_name": None,
                    "recv_source_call_id": None,
                    "result_id": None,
                    "local_id": None,
                    "local_fqn": None,
                    "local_name": None,
                    "local_line": None,
                    "local_type_name": None,
                },
                {
                    "call_id": "call:A",
                    "call_kind": "method",
                    "call_name": "A",
                    "call_file": None,
                    "call_line": 10,
                    "callee_id": "m:A",
                    "callee_fqn": "App\\Svc::A",
                    "callee_kind": "Method",
                    "callee_name": "A",
                    "callee_signature": None,
                    "callee_file": None,
                    "callee_start_line": None,
                    "recv_id": None,
                    "recv_value_kind": None,
                    "recv_name": None,
                    "recv_source_call_id": None,
                    "result_id": None,
                    "local_id": None,
                    "local_fqn": None,
                    "local_name": None,
                    "local_line": None,
                    "local_type_name": None,
                },
            ]
        )
        result = build_execution_flow(runner, "m:doWork")
        assert len(result) == 2
        assert result[0].line == 10
        assert result[1].line == 20

    def test_limit_via_count(self):
        """Entry count respects limit."""
        calls = [
            {
                "call_id": f"call:{i}",
                "call_kind": "method",
                "call_name": f"m{i}",
                "call_file": None,
                "call_line": i,
                "callee_id": f"m:{i}",
                "callee_fqn": f"App\\Svc::m{i}",
                "callee_kind": "Method",
                "callee_name": f"m{i}",
                "callee_signature": None,
                "callee_file": None,
                "callee_start_line": None,
                "recv_id": None,
                "recv_value_kind": None,
                "recv_name": None,
                "recv_source_call_id": None,
                "result_id": None,
                "local_id": None,
                "local_fqn": None,
                "local_name": None,
                "local_line": None,
                "local_type_name": None,
            }
            for i in range(10)
        ]
        runner = make_execution_data_runner(calls=calls)
        count = [0]
        build_execution_flow(runner, "m:doWork", count=count, limit=3)
        assert count[0] <= 3


# =============================================================================
# build_method_used_by
# =============================================================================


class TestBuildMethodUsedBy:
    """Tests for the method USED BY orchestrator."""

    def _make_used_by_runner(self) -> MagicMock:
        """Return a runner that returns empty data for all class-context queries."""
        runner = MagicMock()
        runner.execute.return_value = []
        return runner

    def test_empty_returns_empty(self):
        node = make_method_node()
        runner = self._make_used_by_runner()
        result = build_method_used_by(runner, node)
        assert result == []

    def test_returns_list(self):
        node = make_method_node()
        runner = self._make_used_by_runner()
        result = build_method_used_by(runner, node)
        assert isinstance(result, list)

    def test_limit_applied(self):
        node = make_method_node()
        runner = self._make_used_by_runner()
        result = build_method_used_by(runner, node, limit=5)
        assert len(result) <= 5


# =============================================================================
# Polymorphic: get_implementations_for_node
# =============================================================================


class TestGetImplementationsForNode:
    """Tests for polymorphic implementation discovery."""

    def test_class_node_returns_children(self):
        node = make_class_node()
        runner = MagicMock()
        runner.execute.return_value = [
            {"id": "cls:Child", "fqn": "App\\ChildClass", "kind": "Class",
             "file": "src/Child.php", "start_line": 5, "signature": None}
        ]
        count = [0]
        result = get_implementations_for_node(
            runner, node, depth=1, max_depth=3, limit=100,
            visited=set(), count=count, shown_impl_for=set()
        )
        assert len(result) == 1
        assert result[0].fqn == "App\\ChildClass"
        assert result[0].ref_type == "implements"

    def test_method_node_returns_overrides(self):
        node = make_method_node()
        runner = MagicMock()

        def execute_side_effect(query, **kwargs):
            from src.logic.polymorphic import (
                _Q_DIRECT_OVERRIDES,
                _Q_CONCRETE_IMPLEMENTORS_DIRECT,
                _Q_CONCRETE_IMPLEMENTORS_TRANSITIVE,
            )
            q = query.strip()
            if _Q_DIRECT_OVERRIDES.strip() in q or q in _Q_DIRECT_OVERRIDES.strip():
                return [
                    {"id": "m:override", "fqn": "App\\Child::doWork", "kind": "Method",
                     "file": "src/Child.php", "start_line": 10, "signature": None}
                ]
            if _Q_CONCRETE_IMPLEMENTORS_DIRECT.strip() in q or q in _Q_CONCRETE_IMPLEMENTORS_DIRECT.strip():
                return []
            if _Q_CONCRETE_IMPLEMENTORS_TRANSITIVE.strip() in q or q in _Q_CONCRETE_IMPLEMENTORS_TRANSITIVE.strip():
                return []
            return []

        runner.execute.side_effect = execute_side_effect
        count = [0]
        result = get_implementations_for_node(
            runner, node, depth=1, max_depth=3, limit=100,
            visited=set(), count=count, shown_impl_for=set()
        )
        assert len(result) == 1
        assert result[0].ref_type == "overrides"
        assert result[0].fqn == "App\\Child::doWork"

    def test_shown_impl_for_dedup(self):
        """If node_id is already in shown_impl_for, returns empty."""
        node = make_class_node()
        runner = MagicMock()
        runner.execute.return_value = [
            {"id": "cls:Child", "fqn": "App\\ChildClass", "kind": "Class",
             "file": None, "start_line": None, "signature": None}
        ]
        shown = {node.node_id}  # Already shown
        count = [0]
        result = get_implementations_for_node(
            runner, node, depth=1, max_depth=3, limit=100,
            visited=set(), count=count, shown_impl_for=shown
        )
        assert result == []
        runner.execute.assert_not_called()

    def test_limit_respected(self):
        node = make_class_node()
        runner = MagicMock()
        runner.execute.return_value = [
            {"id": f"cls:{i}", "fqn": f"App\\Child{i}", "kind": "Class",
             "file": None, "start_line": None, "signature": None}
            for i in range(10)
        ]
        count = [100]  # Already at limit (count >= limit)
        result = get_implementations_for_node(
            runner, node, depth=1, max_depth=3, limit=100,
            visited=set(), count=count, shown_impl_for=set()
        )
        assert result == []


# =============================================================================
# Polymorphic: get_interface_method_ids
# =============================================================================


class TestGetInterfaceMethodIds:
    """Tests for get_interface_method_ids."""

    def test_empty_returns_empty(self):
        runner = MagicMock()
        runner.execute.return_value = []
        result = get_interface_method_ids(runner, "m:save")
        assert result == []

    def test_returns_interface_method_ids(self):
        runner = MagicMock()
        runner.execute.return_value = [
            {"id": "m:iface_save"},
            {"id": "m:iface_save2"},
        ]
        result = get_interface_method_ids(runner, "m:save")
        assert result == ["m:iface_save", "m:iface_save2"]

    def test_filters_none_ids(self):
        runner = MagicMock()
        runner.execute.return_value = [
            {"id": "m:iface_save"},
            {"id": None},
        ]
        result = get_interface_method_ids(runner, "m:save")
        assert result == ["m:iface_save"]


# =============================================================================
# Polymorphic: get_concrete_implementors
# =============================================================================


class TestGetConcreteImplementors:
    """Tests for get_concrete_implementors."""

    def test_empty_returns_empty(self):
        runner = MagicMock()
        runner.execute.return_value = []
        result = get_concrete_implementors(runner, "m:iface_save")
        assert result == []

    def test_returns_implementor_method_ids(self):
        runner = MagicMock()
        runner.execute.return_value = [
            {"id": "m:impl_save_1"},
            {"id": "m:impl_save_2"},
        ]
        result = get_concrete_implementors(runner, "m:iface_save")
        assert result == ["m:impl_save_1", "m:impl_save_2"]

    def test_filters_none_ids(self):
        runner = MagicMock()
        runner.execute.return_value = [
            {"id": "m:impl_save"},
            {"id": None},
        ]
        result = get_concrete_implementors(runner, "m:iface_save")
        assert result == ["m:impl_save"]
