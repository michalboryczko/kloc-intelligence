"""Method context orchestrators: build USED BY and execution flow for Method nodes.

Ported from kloc-cli's method context logic. Each public function fetches
pre-resolved data from Neo4j (via batch query functions) then applies pure
logic to produce lists of ContextEntry objects.

Key design rules:
- Execution flow produces Kind 1 (local_variable) and Kind 2 (call) entries.
- Kind 1: local variable assigned from a call result. source_call is a nested
  ContextEntry (not a dict).
- Kind 2: standalone call. Consumed calls (used as receiver/argument by another
  call) are excluded from the top-level list.
- MemberRef is populated for execution flow entries (target_name, target_fqn,
  target_kind, reference_type, access_chain, on_kind).
- Cycle guard prevents infinite recursion when recursing into callee methods.
- External calls (no callee_id) build FQN from call node data; cannot recurse.
- Orphan property_access entries whose expression appears in an argument are
  filtered out.
- All line numbers are 0-based.
"""

from __future__ import annotations

from ..db.query_runner import QueryRunner
from ..db.queries.context_method import (
    Q4_TYPE_REFERENCES,
    fetch_method_execution_data,
)
from ..db.queries.context_class import fetch_class_used_by_data
from ..logic.graph_helpers import format_method_fqn
from ..models.node import NodeData
from ..models.results import ContextEntry, MemberRef, ArgumentInfo
from .value_context import resolve_promoted_property_fqn

# Trace source chain for a result Value node
_Q_SOURCE_CHAIN = """
MATCH (val:Value {node_id: $value_id})<-[:PRODUCES]-(call:Call)-[:CALLS]->(target)
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
RETURN target.fqn AS target_fqn,
       target.kind AS target_kind,
       call.call_kind AS call_kind,
       recv.fqn AS recv_fqn,
       recv.value_kind AS recv_value_kind,
       recv.file AS recv_file,
       recv.start_line AS recv_start_line
LIMIT 1
"""


def _trace_source_chain(runner: QueryRunner, value_node_id: str) -> list | None:
    """Trace the source chain for a result Value node."""
    rec = runner.execute_single(_Q_SOURCE_CHAIN, value_id=value_node_id)
    if not rec:
        return None

    target_fqn = rec.get("target_fqn")
    target_kind = rec.get("target_kind")
    call_kind = rec.get("call_kind")
    recv_fqn = rec.get("recv_fqn")
    recv_value_kind = rec.get("recv_value_kind")
    recv_file = rec.get("recv_file")
    recv_start_line = rec.get("recv_start_line")

    if not target_fqn:
        return None

    step: dict = {
        "fqn": target_fqn,
        "kind": target_kind,
        "reference_type": call_kind,
    }

    if recv_fqn:
        step["on"] = recv_fqn
        if recv_value_kind == "local":
            step["on_kind"] = "local"
        elif recv_value_kind == "parameter":
            step["on_kind"] = "param"
        if recv_file:
            step["on_file"] = recv_file
        if recv_start_line is not None:
            step["on_line"] = recv_start_line

    return [step]


# =============================================================================
# Receiver identity resolution
# =============================================================================


def _resolve_receiver_identity(
    recv_value_kind: str | None,
    recv_name: str | None,
    call_id: str | None,
    runner: QueryRunner | None = None,
    recv_file: str | None = None,
    recv_start_line: int | None = None,
) -> tuple[str | None, str | None, str | None, str | None, int | None]:
    """Resolve access_chain, access_chain_symbol, on_kind, on_file, on_line for a call.

    Follows the reference implementation's resolve_receiver_identity pattern:
    - Builds the access_chain expression (e.g., "$this->orderService")
    - Resolves the access_chain_symbol FQN (e.g., "App\\...::$orderService")
    - Determines on_kind from the receiver Value node type
    - Returns on_file/on_line from the receiver Value node (for local/param receivers)

    Rules:
    - recv_value_kind == "parameter" -> on_kind="param", ac=recv_name
    - recv_value_kind == "local"     -> on_kind="local", ac=recv_name
    - recv_value_kind == "self"      -> on_kind="self", ac=None
    - recv_value_kind == "result"    -> on_kind="property", ac="$this->propName"
    - No receiver                    -> on_kind=None

    Args:
        recv_value_kind: The value_kind of the receiver Value node.
        recv_name: The name of the receiver Value node.
        call_id: Node ID of the call (used to run Q5 if needed).
        runner: QueryRunner for Q5 lookup.
        recv_file: File of the receiver Value node.
        recv_start_line: Start line of the receiver Value node.

    Returns:
        Tuple of (access_chain, access_chain_symbol, on_kind, on_file, on_line).
    """
    if recv_value_kind is None:
        # No explicit receiver — check if this is an implicit $this call
        if runner and call_id:
            from ..db.queries.context_method import Q5_RECEIVER_CHAIN
            # If there's no receiver but it's a method call, it could be self
            rec = runner.execute_single("""
                MATCH (call:Node {node_id: $call_id})
                RETURN call.call_kind AS call_kind
            """, call_id=call_id)
            if rec and rec.get("call_kind") in ("access", "method", "method_static"):
                return "$this", None, "self", None, None
        return None, None, None, None, None

    if recv_value_kind == "parameter":
        return recv_name, None, "param", recv_file, recv_start_line

    if recv_value_kind == "local":
        return recv_name, None, "local", recv_file, recv_start_line

    if recv_value_kind == "self":
        return "$this", None, "self", None, None

    if recv_value_kind == "result":
        # Try to resolve via Q5 to get the property chain
        if runner and call_id:
            from ..db.queries.context_method import Q5_RECEIVER_CHAIN
            records = runner.execute(Q5_RECEIVER_CHAIN, call_id=call_id)
            for r in records:
                prop_fqn = r.get("prop_fqn")
                prop_name = r.get("prop_name")
                src_call_kind = r.get("src_call_kind")
                src_recv_kind = r.get("src_recv_kind")
                src_recv_name = r.get("src_recv_name")

                if prop_name:
                    member = prop_name.lstrip("$")
                    # Build expression from source receiver
                    if src_recv_kind == "self" or src_recv_kind is None:
                        chain = f"$this->{member}"
                    elif src_recv_kind == "parameter":
                        chain = f"{src_recv_name}->{member}"
                    elif src_recv_kind == "local":
                        chain = f"{src_recv_name}->{member}"
                    else:
                        chain = f"$this->{member}"

                    if src_call_kind in ("method", "method_static"):
                        chain += "()"

                    return chain, prop_fqn, "property", None, None
        return recv_name, None, "result", None, None

    return recv_name, None, recv_value_kind, None, None


def _build_member_ref(
    callee_fqn: str | None,
    callee_name: str | None,
    callee_kind: str | None,
    access_chain: str | None,
    access_chain_symbol: str | None,
    on_kind: str | None,
    call_kind: str | None = None,
    call_file: str | None = None,
    call_line: int | None = None,
    on_file: str | None = None,
    on_line: int | None = None,
) -> MemberRef | None:
    """Build a MemberRef for a call entry.

    Args:
        callee_fqn: FQN of the callee.
        callee_name: Name of the callee.
        callee_kind: Kind of the callee.
        access_chain: Receiver expression (e.g., "$this->orderService").
        access_chain_symbol: FQN of the access chain property.
        on_kind: Receiver kind (e.g., "property", "param", "self").
        call_kind: Call kind from Call node (e.g., "method", "constructor").
        call_file: File where the call occurs.
        call_line: Line where the call occurs (0-based).
        on_file: File of the receiver Value node.
        on_line: Start line of the receiver Value node (0-based).

    Returns:
        MemberRef if callee data is available, else None.
    """
    if not callee_fqn and not callee_name:
        return None

    target_name = callee_name or ""
    if callee_kind == "Method" and not target_name.endswith("()"):
        display_name = target_name + "()"
    else:
        display_name = target_name

    # Resolve reference_type from call_kind if available
    _CALL_KIND_MAP = {
        "method": "method_call",
        "method_static": "static_call",
        "constructor": "instantiation",
        "access": "property_access",
        "access_static": "static_property",
        "function": "function_call",
    }
    if call_kind and call_kind in _CALL_KIND_MAP:
        reference_type = _CALL_KIND_MAP[call_kind]
    elif callee_kind == "Method":
        reference_type = "method_call"
    elif callee_kind == "Property":
        reference_type = "property_access"
    elif callee_kind == "Function":
        reference_type = "function_call"
    else:
        reference_type = "method_call"

    # For external calls, infer target_kind from call_kind
    target_kind = callee_kind
    if target_kind is None and call_kind:
        target_kind = call_kind  # e.g., "method", "function"

    return MemberRef(
        target_name=display_name,
        target_fqn=callee_fqn or "",
        target_kind=target_kind,
        file=call_file,
        line=call_line,
        reference_type=reference_type,
        access_chain=access_chain,
        access_chain_symbol=access_chain_symbol,
        on_kind=on_kind,
        on_file=on_file,
        on_line=on_line,
    )


def _build_argument_infos(
    call_id: str,
    args_by_call: dict[str, list[dict]],
    runner: QueryRunner | None = None,
) -> list[ArgumentInfo]:
    """Build ArgumentInfo list for a call from pre-fetched argument data.

    Args:
        call_id: Node ID of the call.
        args_by_call: Index of argument records keyed by call_id.
        runner: Optional QueryRunner for source_chain tracing.

    Returns:
        List of ArgumentInfo objects ordered by position.
    """
    arg_records = args_by_call.get(call_id, [])
    infos: list[ArgumentInfo] = []
    for rec in arg_records:
        position = rec.get("position")
        if position is None:
            continue
        raw_param_fqn = rec.get("parameter")
        # Resolve promoted constructor params to Property FQNs
        param_fqn = raw_param_fqn
        if raw_param_fqn and runner is not None:
            resolved = resolve_promoted_property_fqn(runner, raw_param_fqn)
            if resolved:
                param_fqn = resolved
        # Derive param_name from the param FQN: "Class::method().$name" -> "$name"
        param_name = None
        if param_fqn:
            if ".$" in param_fqn:
                param_name = param_fqn.rsplit(".", 1)[-1]
            elif "$" in param_fqn:
                param_name = "$" + param_fqn.rsplit("$", 1)[-1]

        # value_ref_symbol: the value's FQN (if it's a proper symbol)
        value_fqn = rec.get("value_fqn")
        value_ref_symbol = None
        if value_fqn and "::" in value_fqn:
            value_ref_symbol = value_fqn

        # Trace source chain for result values
        source_chain = None
        value_kind = rec.get("value_kind")
        if value_kind == "result" and runner is not None:
            value_node_id = rec.get("value_id")
            if value_node_id:
                source_chain = _trace_source_chain(runner, value_node_id)

        infos.append(ArgumentInfo(
            position=int(position),
            param_name=param_name,
            param_fqn=param_fqn,
            value_expr=rec.get("expression"),
            value_source=value_kind,
            value_type=rec.get("value_type"),
            value_ref_symbol=value_ref_symbol,
            source_chain=source_chain,
        ))
    infos.sort(key=lambda a: a.position)
    return infos


# =============================================================================
# get_type_references
# =============================================================================


def get_type_references(
    runner: QueryRunner,
    method_id: str,
    depth: int = 1,
    cycle_guard: set[str] | None = None,
    count: list[int] | None = None,
    limit: int = 100,
) -> list[ContextEntry]:
    """Return type references used by a method that aren't covered by Call nodes.

    Uses Q4 (TYPE_REFERENCES). Returns parameter_type, return_type, and type_hint
    entries. These typically precede execution flow entries.

    Args:
        runner: Active QueryRunner.
        method_id: Node ID of the method.
        depth: Depth to assign to returned entries.
        cycle_guard: Set of visited node IDs (not used here but passed for API compat).
        count: Mutable single-element list for total entry tracking.
        limit: Maximum entries to return.

    Returns:
        List of ContextEntry objects for type references.
    """
    # Only include property_type and type_hint — parameter_type and return_type
    # are already shown in the DEFINITION section (matching reference impl).
    TYPE_KINDS = {"property_type", "type_hint"}

    # Pre-fetch set of class IDs that have a corresponding constructor Call node
    # in this method. Those will be covered by execution flow, so skip them here.
    # Only matches constructor calls (call_kind = 'constructor'), not regular method calls.
    _Q_CONSTRUCTOR_TARGETS = """
    MATCH (method:Node {node_id: $method_id})-[:CONTAINS]->(call:Call {call_kind: 'constructor'})-[:CALLS]->(callee)
    MATCH (callee)<-[:CONTAINS]-(parent_cls)
    RETURN DISTINCT parent_cls.node_id AS class_id
    """
    ctor_records = runner.execute(_Q_CONSTRUCTOR_TARGETS, method_id=method_id)
    call_covered_ids: set[str] = set()
    for cr in ctor_records:
        cid = cr.get("class_id")
        if cid:
            call_covered_ids.add(cid)

    records = runner.execute(Q4_TYPE_REFERENCES, method_id=method_id)
    entries: list[ContextEntry] = []
    seen: set[str] = set()

    for r in records:
        target_id = r.get("target_id")
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)

        # Skip if there's a Call node in this method targeting this class
        # (constructor calls are handled by execution flow)
        if target_id in call_covered_ids:
            continue

        has_arg_th = bool(r.get("has_arg_th", False))
        has_ret_th = bool(r.get("has_ret_th", False))

        if has_arg_th:
            ref_type = "parameter_type"
        elif has_ret_th:
            ref_type = "return_type"
        else:
            ref_type = "type_hint"

        # Skip parameter_type and return_type — only keep property_type/type_hint
        if ref_type not in TYPE_KINDS:
            continue

        target_fqn = r.get("target_fqn", "")
        target_kind = r.get("target_kind")
        target_name = target_fqn.rsplit("\\", 1)[-1] if "\\" in target_fqn else target_fqn
        entry_file = r.get("file")
        entry_line = r.get("line")

        member_ref = MemberRef(
            target_name=target_name,
            target_fqn=target_fqn,
            target_kind=target_kind,
            file=entry_file,
            line=entry_line,
            reference_type=ref_type,
        )

        entry = ContextEntry(
            depth=depth,
            node_id=target_id,
            fqn=target_fqn,
            kind=target_kind,
            file=entry_file,
            line=entry_line,
            signature=r.get("target_signature"),
            member_ref=member_ref,
        )
        entries.append(entry)
        if count is not None:
            count[0] += 1
        if count is not None and count[0] >= limit:
            break

    return entries


# =============================================================================
# filter_orphan_property_accesses
# =============================================================================


def filter_orphan_property_accesses(entries: list[ContextEntry]) -> list[ContextEntry]:
    """Filter Kind 2 property_access entries whose expression appears in arguments.

    An "orphan" property access is a call entry (entry_type="call") with a
    property_access reference where the access expression already appears as
    a value_expr in another entry's arguments. These are redundant because
    the property access is already represented as an argument.

    Only filters entries with:
    - entry_type == "call"
    - member_ref.reference_type == "property_access"

    Args:
        entries: List of ContextEntry objects.

    Returns:
        Filtered list with orphan property access entries removed.
    """
    # Collect all value_expr strings from arguments across all entries
    all_value_exprs: list[str] = []
    for entry in entries:
        for arg in entry.arguments:
            if arg.value_expr:
                all_value_exprs.append(arg.value_expr)
        if entry.source_call:
            for arg in entry.source_call.arguments:
                if arg.value_expr:
                    all_value_exprs.append(arg.value_expr)

    if not all_value_exprs:
        return entries

    result: list[ContextEntry] = []
    for entry in entries:
        # Only consider Kind 2 property_access entries as orphan candidates
        if (
            entry.entry_type == "call"
            and entry.member_ref is not None
            and entry.member_ref.reference_type == "property_access"
            and entry.member_ref.access_chain
        ):
            # Build the expression: "$receiver->propertyName"
            # FQN is like "App\Entity\Order::$id", extract "id"
            prop_fqn = entry.fqn
            prop_name = prop_fqn.split("::$")[-1] if "::$" in prop_fqn else None
            if prop_name:
                access_expr = f"{entry.member_ref.access_chain}->{prop_name}"
                # Check if this expression appears in any value_expr
                is_expression_consumed = any(
                    access_expr in expr for expr in all_value_exprs
                )
                if is_expression_consumed:
                    # Orphan: skip this entry
                    continue
        result.append(entry)
    return result


# =============================================================================
# build_execution_flow (core method USES builder)
# =============================================================================


def build_execution_flow(
    runner: QueryRunner,
    method_id: str,
    depth: int = 1,
    max_depth: int = 3,
    limit: int = 100,
    cycle_guard: set[str] | None = None,
    count: list[int] | None = None,
) -> list[ContextEntry]:
    """Build execution flow entries for a Method node.

    Fetches all calls within the method and produces Kind 1 (local_variable)
    or Kind 2 (call) ContextEntry objects. Consumed calls (used as receiver
    or argument by another call) are excluded from the top-level list.

    Kind 1 (local_id present):
        - source_call: ContextEntry for the call that produces the local var
        - entry_type: "local_variable"
        - variable_name, variable_symbol, variable_type from local var data

    Kind 2 (no local_id):
        - entry_type: "call"
        - member_ref populated with callee info
        - arguments list

    Depth expansion: recurse into callee's execution flow at depth+1 (up to
    max_depth). Each recursion adds callee_id to cycle_guard.

    Args:
        runner: Active QueryRunner.
        method_id: Node ID of the method.
        depth: Current depth (starts at 1 for top-level calls).
        max_depth: Maximum recursion depth.
        limit: Maximum entries limit (shared via count).
        cycle_guard: Set of node IDs visited in this execution flow traversal.
        count: Mutable [int] tracking total entries emitted.

    Returns:
        List of ContextEntry objects for the method's execution flow.
    """
    if cycle_guard is None:
        cycle_guard = set()
    if count is None:
        count = [0]

    if depth > max_depth:
        return []

    data = fetch_method_execution_data(runner, method_id)
    calls: list[dict] = data["calls"]
    arguments: list[dict] = data["arguments"]
    consumed_ids: set[str] = data["consumed_ids"]

    # Build args index: call_id -> list of arg records
    args_by_call: dict[str, list[dict]] = {}
    for arg_rec in arguments:
        cid = arg_rec.get("call_id")
        if cid:
            args_by_call.setdefault(cid, []).append(arg_rec)

    entries: list[ContextEntry] = []
    local_visited: set[str] = set()

    for call_rec in calls:
        if count[0] >= limit:
            break

        call_id = call_rec.get("call_id")
        if not call_id:
            continue

        # Skip consumed calls (they appear as receiver/argument of another call)
        if call_id in consumed_ids:
            continue

        callee_id = call_rec.get("callee_id")
        callee_fqn = call_rec.get("callee_fqn")
        callee_kind = call_rec.get("callee_kind")
        callee_name = call_rec.get("callee_name")
        callee_signature = call_rec.get("callee_signature")

        # Skip if already visited in this local pass
        if callee_id and callee_id in local_visited:
            continue
        # Skip if in cycle guard (prevents cross-method cycles)
        if callee_id and callee_id in cycle_guard:
            continue

        if callee_id:
            local_visited.add(callee_id)

        # Resolve receiver identity
        recv_value_kind = call_rec.get("recv_value_kind")
        recv_name = call_rec.get("recv_name")
        recv_file = call_rec.get("recv_file")
        recv_start_line = call_rec.get("recv_start_line")
        access_chain, access_chain_symbol, on_kind, on_file, on_line = _resolve_receiver_identity(
            recv_value_kind, recv_name, call_id, runner, recv_file, recv_start_line
        )

        # Build arguments
        arg_infos = _build_argument_infos(call_id, args_by_call, runner)

        local_id = call_rec.get("local_id")
        call_file = call_rec.get("call_file")
        call_line = call_rec.get("call_line")
        call_kind = call_rec.get("call_kind")

        if local_id:
            # -------------------------------------------------------
            # Kind 1: local variable assigned from this call's result
            # -------------------------------------------------------
            # Build inner source_call entry
            member_ref = _build_member_ref(
                callee_fqn, callee_name, callee_kind,
                access_chain, access_chain_symbol, on_kind,
                call_kind, call_file, call_line,
                on_file, on_line,
            )
            source_call_entry = ContextEntry(
                depth=depth,
                node_id=call_id,
                fqn=format_method_fqn(callee_fqn or call_rec.get("call_name") or "", callee_kind or ""),
                kind=callee_kind,
                file=call_file,
                line=call_line,
                signature=callee_signature,
                entry_type="call",
                member_ref=member_ref,
                arguments=arg_infos,
            )

            # Outer local_variable entry — count before recursing so sub-calls
            # see the correct running total when enforcing the limit.
            local_entry = ContextEntry(
                depth=depth,
                node_id=local_id,
                fqn=call_rec.get("local_fqn") or local_id,
                kind="Value",
                file=call_file,
                line=call_rec.get("local_line"),
                entry_type="local_variable",
                variable_name=call_rec.get("local_name"),
                variable_symbol=call_rec.get("local_fqn"),
                variable_type=call_rec.get("local_type_name"),
                source_call=source_call_entry,
            )
            entries.append(local_entry)
            count[0] += 1

            # Recurse into callee execution flow — children go on the
            # local_variable entry (local_entry), not on source_call_entry.
            if callee_id and depth < max_depth:
                new_cycle_guard = set(cycle_guard)
                new_cycle_guard.add(callee_id)
                local_entry.children = build_execution_flow(
                    runner,
                    callee_id,
                    depth=depth + 1,
                    max_depth=max_depth,
                    limit=limit,
                    cycle_guard=new_cycle_guard,
                    count=count,
                )

        else:
            # -------------------------------------------------------
            # Kind 2: standalone call (not assigned to a local var)
            # -------------------------------------------------------
            # For external calls (no callee_id), build FQN from call data
            if callee_id is None:
                call_fqn = callee_fqn or call_rec.get("call_name") or ""
            else:
                call_fqn = callee_fqn or ""

            member_ref = _build_member_ref(
                callee_fqn or call_rec.get("call_name"),
                callee_name or call_rec.get("call_name"),
                callee_kind,
                access_chain, access_chain_symbol, on_kind,
                call_kind, call_file, call_line,
                on_file, on_line,
            )

            # For external calls, infer kind from call_kind
            entry_kind = callee_kind
            if entry_kind is None and call_kind:
                entry_kind = call_kind  # e.g., "method", "function"

            call_entry = ContextEntry(
                depth=depth,
                node_id=callee_id or call_id,
                fqn=format_method_fqn(call_fqn, callee_kind or ""),
                kind=entry_kind,
                file=call_file,
                line=call_line,
                signature=callee_signature,
                entry_type="call",
                member_ref=member_ref,
                arguments=arg_infos,
            )

            # Append and count before recursing so sub-calls see the correct
            # running total when enforcing the shared limit.
            entries.append(call_entry)
            count[0] += 1

            # Recurse into callee execution flow (only for known callees)
            if callee_id and depth < max_depth:
                new_cycle_guard = set(cycle_guard)
                new_cycle_guard.add(callee_id)
                call_entry.children = build_execution_flow(
                    runner,
                    callee_id,
                    depth=depth + 1,
                    max_depth=max_depth,
                    limit=limit,
                    cycle_guard=new_cycle_guard,
                    count=count,
                )

    # Filter orphan property accesses
    entries = filter_orphan_property_accesses(entries)

    # Sort by call line number
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

    return entries


# =============================================================================
# build_method_used_by
# =============================================================================


def build_method_used_by(
    runner: QueryRunner,
    node: NodeData,
    max_depth: int = 1,
    limit: int = 100,
) -> list[ContextEntry]:
    """Build the USED BY section for a Method node context.

    Reuses the class USED BY machinery with the method as the target node.
    Methods can be called by other methods but cannot be extended/implemented,
    so extends/implements entries are excluded.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node: NodeData for the target method.
        max_depth: Maximum depth for caller chain expansion.
        limit: Maximum number of entries to return.

    Returns:
        Ordered list of ContextEntry objects representing who calls the method.
    """
    from ..logic.reference_types import infer_reference_type, CHAINABLE_REFERENCE_TYPES
    from ..logic.handlers import EdgeContext, EntryBucket, USED_BY_HANDLERS
    from .class_context import _dict_to_context_entry, build_caller_chain_for_method

    data = fetch_class_used_by_data(runner, node.node_id)

    incoming_usages: list[dict] = data["incoming_usages"]
    call_nodes: list[dict] = data["call_nodes"]
    ref_type_data: dict[str, dict] = data["ref_type_data"]

    # Build call node index
    call_index: dict[tuple, dict] = {}
    for cn in call_nodes:
        key = (cn["source_id"], cn.get("call_file"), cn.get("call_start_line"))
        if key not in call_index:
            call_index[key] = cn

    # Process incoming usage edges
    bucket = EntryBucket()
    classes_with_injection: frozenset[str] = frozenset()  # No injection suppression

    for usage in incoming_usages:
        source_id: str = usage["source_id"]
        source_kind: str = usage.get("source_kind", "")
        source_fqn: str = usage.get("source_fqn", "")
        source_name: str = usage.get("source_name", "") or ""
        source_file = usage.get("source_file")
        source_start_line = usage.get("source_start_line")
        source_signature = usage.get("source_signature")
        edge_type: str = usage.get("edge_type", "USES").upper()
        edge_file = usage.get("edge_file")
        edge_line = usage.get("edge_line")
        containing_class_id = usage.get("containing_class_id")
        containing_method_id = usage.get("containing_method_id")
        containing_method_fqn = usage.get("containing_method_fqn")
        containing_method_kind = usage.get("containing_method_kind")

        # Skip extends/implements (methods can't be extended)
        edge_type_upper = edge_type.upper()
        if edge_type_upper in ("EXTENDS", "IMPLEMENTS", "USES_TRAIT"):
            continue

        rtd = ref_type_data.get(source_id, {})
        has_arg_type_hint: bool = bool(rtd.get("has_arg_type_hint", False))
        has_return_type_hint: bool = bool(rtd.get("has_return_type_hint", False))
        has_class_property_type_hint: bool = bool(rtd.get("has_class_property_type_hint", False))
        has_source_class_property_type_hint: bool = bool(
            rtd.get("has_source_class_property_type_hint", False)
        )

        edge_type_lower = edge_type.lower()
        ref_type = infer_reference_type(
            edge_type=edge_type_lower,
            target_kind=node.kind,
            source_kind=source_kind,
            source_id=source_id,
            target_id=node.node_id,
            source_name=source_name,
            has_arg_type_hint=has_arg_type_hint,
            has_return_type_hint=has_return_type_hint,
            has_class_property_type_hint=has_class_property_type_hint,
            has_source_class_property_type_hint=has_source_class_property_type_hint,
        )

        call_key = (source_id, edge_file, edge_line)
        call_record = call_index.get(call_key)
        call_node_id = call_record["call_id"] if call_record else None
        call_kind = call_record["call_kind"] if call_record else None

        if call_kind == "constructor":
            ref_type = "instantiation"

        access_chain: str | None = None
        on_kind: str | None = None
        if call_record:
            recv_value_kind = call_record.get("recv_value_kind")
            recv_name = call_record.get("recv_name")
            if recv_name:
                access_chain = recv_name
            if recv_value_kind:
                on_kind = recv_value_kind

        ctx = EdgeContext(
            start_id=node.node_id,
            source_id=source_id,
            source_kind=source_kind,
            source_fqn=source_fqn,
            source_name=source_name,
            source_file=source_file,
            source_start_line=source_start_line,
            source_signature=source_signature,
            target_kind=node.kind,
            target_fqn=node.fqn,
            target_name=node.name,
            ref_type=ref_type,
            file=edge_file,
            line=edge_line,
            call_node_id=call_node_id,
            classes_with_injection=classes_with_injection,
            containing_method_id=containing_method_id,
            containing_method_fqn=containing_method_fqn,
            containing_method_kind=containing_method_kind,
            containing_class_id=containing_class_id,
            call_kind=call_kind,
            access_chain=access_chain,
            on_kind=on_kind,
        )

        handler = USED_BY_HANDLERS.get(ref_type)
        if handler:
            handler.handle(ctx, bucket)

    # Convert buckets to ContextEntry lists
    result_method_call = [_dict_to_context_entry(d) for d in bucket.method_call]
    result_instantiation = [_dict_to_context_entry(d) for d in bucket.instantiation]
    result_param_return = [_dict_to_context_entry(d) for d in bucket.param_return]

    # Depth 2+: expand caller chains
    if max_depth >= 2:
        visited_methods: set[str] = set()
        for entry in result_method_call:
            if entry.node_id and entry.ref_type in CHAINABLE_REFERENCE_TYPES:
                visited_methods.add(entry.node_id)
                entry.children = build_caller_chain_for_method(
                    runner, entry.node_id, 2, max_depth, visited_methods
                )

    all_entries = result_method_call + result_instantiation + result_param_return
    return all_entries[:limit]
