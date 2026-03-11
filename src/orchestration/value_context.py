"""Value context orchestrators: build consumer and source chains for Value nodes.

Ported from kloc-cli's value_context.py. Handles Value consumer chains (USED BY)
and source chains (USES), including cross-method boundary tracing via parameter
FQNs and return value type matching.

Key design rules:
- All Neo4j access is via query functions in src/db/queries/context_value.py.
- Visited set prevents infinite cycles across Value nodes.
- seen_calls prevents duplicate entries for the same Call.
- Cross-method boundary via parameter FQN matching (callee direction).
- Cross-method return via type matching (caller direction).
- max_crossings limits return crossings per chain (defaults to min(max_depth, 10)).
- All line numbers are 0-based.
"""

from __future__ import annotations

from ..db.query_runner import QueryRunner
from ..db.queries.context_value import (
    Q4_ARGUMENT_PARAMS,
    Q5_RESOLVE_PARAM,
    Q6_LOCAL_FOR_RESULT,
    Q7_TYPE_OF,
    Q8_METHOD_CALLERS,
    Q10_PARAMETER_USES,
    Q11_CALL_ARGUMENTS,
    Q12_CONTAINING_METHOD,
    fetch_value_consumer_data,
    fetch_value_source_data,
)
from ..models.results import ContextEntry, MemberRef, ArgumentInfo

# Query to get Call node call_kind for implicit $this detection
_Q_CALL_KIND = """
MATCH (c:Node {node_id: $call_id})
RETURN c.call_kind AS call_kind
"""

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
# Promoted parameter resolution
# =============================================================================

# Query to resolve a promoted constructor parameter to its Property FQN.
# For PHP constructor promotion: Property -[ASSIGNED_FROM]-> Value(parameter).
_Q_RESOLVE_PROMOTED_PARAM = """
MATCH (prop:Node {kind: 'Property'})-[:ASSIGNED_FROM]->(param:Value {value_kind: 'parameter'})
WHERE param.fqn = $param_fqn
RETURN prop.fqn AS property_fqn
LIMIT 1
"""

# Module-level cache for promoted param resolution to avoid N+1 queries
_promoted_param_cache: dict[str, str | None] = {}


def resolve_promoted_property_fqn(runner: QueryRunner, param_fqn: str) -> str | None:
    """Resolve a promoted constructor parameter FQN to its Property FQN.

    For PHP constructor promotion, the parameter Value node has an
    ASSIGNED_FROM edge from a Property node. Returns the Property FQN.

    Uses a module-level cache to avoid repeated queries for the same param_fqn.

    Args:
        runner: Active QueryRunner.
        param_fqn: The parameter FQN (e.g., 'Order::__construct().$id').

    Returns:
        Property FQN if promoted (e.g., 'Order::$id'), None otherwise.
    """
    if param_fqn in _promoted_param_cache:
        return _promoted_param_cache[param_fqn]

    rec = runner.execute_single(_Q_RESOLVE_PROMOTED_PARAM, param_fqn=param_fqn)
    result = rec.get("property_fqn") if rec else None
    _promoted_param_cache[param_fqn] = result
    return result


# =============================================================================
# Internal helpers
# =============================================================================


def _build_callee_display(name: str | None, kind: str | None) -> str | None:
    """Format callee name for display (e.g., 'save()' for Method)."""
    if not name:
        return None
    if kind == "Method":
        return name + "()"
    if kind == "Property":
        return name if name.startswith("$") else "$" + name
    return name


def _build_arguments_from_records(
    runner: QueryRunner,
    call_id: str,
) -> list[ArgumentInfo]:
    """Build ArgumentInfo list for a call by querying Q11."""
    records = runner.execute(Q11_CALL_ARGUMENTS, call_id=call_id)
    infos: list[ArgumentInfo] = []
    for rec in records:
        position = rec.get("position")
        if position is None:
            continue

        raw_param_fqn = rec.get("parameter")
        # Resolve promoted constructor params to Property FQNs
        param_fqn = raw_param_fqn
        if raw_param_fqn:
            resolved = resolve_promoted_property_fqn(runner, raw_param_fqn)
            if resolved:
                param_fqn = resolved

        # Derive param_name from param FQN: "Class::method().$name" -> "$name"
        param_name = None
        if param_fqn:
            if ".$" in param_fqn:
                param_name = param_fqn.rsplit(".", 1)[-1]
            elif "$" in param_fqn:
                param_name = "$" + param_fqn.rsplit("$", 1)[-1]

        # value_ref_symbol: the value's FQN if it's a proper symbol reference
        value_fqn = rec.get("value_fqn")
        value_ref_symbol = None
        if value_fqn and "::" in value_fqn:
            value_ref_symbol = value_fqn

        # Trace source chain for result values
        source_chain = None
        value_kind = rec.get("value_kind")
        if value_kind == "result":
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


def _infer_ref_type_from_call_kind(call_kind: str | None, target_kind: str | None) -> str | None:
    """Infer reference type from call_kind and target_kind."""
    if call_kind == "constructor":
        return "instantiation"
    if target_kind == "Method":
        return "method_call"
    if target_kind == "Property":
        return "property_access"
    if target_kind == "Function":
        return "function_call"
    return None


def _resolve_on_kind(recv_value_kind: str | None) -> str | None:
    """Map receiver value_kind to on_kind display string."""
    if recv_value_kind == "parameter":
        return "param"
    if recv_value_kind == "local":
        return "local"
    if recv_value_kind == "self":
        return "self"
    if recv_value_kind == "result":
        return "property"
    return recv_value_kind


def _resolve_call_on(
    runner: QueryRunner, call_id: str,
) -> tuple[str | None, str | None, str | None, str | None, int | None]:
    """Resolve on/on_kind for a Call node.

    Uses Q3_RECEIVER_IDENTITY to find explicit receiver. Falls back to
    implicit $this detection when no receiver edge exists (matching kloc-cli
    graph_utils.py logic for access/method/method_static calls).

    Returns:
        (on, on_kind, access_chain_symbol, on_file, on_line) tuple.
        access_chain_symbol: FQN of the property in an access chain (e.g. 'Class::$prop').
        on_file/on_line: location of the receiver for local/param receivers.
    """
    from ..db.queries.context_value import Q3_RECEIVER_IDENTITY

    recv_rec = runner.execute_single(Q3_RECEIVER_IDENTITY, call_id=call_id)
    if recv_rec:
        recv_kind = recv_rec.get("recv_kind") if isinstance(recv_rec, dict) else recv_rec["recv_kind"]
        recv_name = recv_rec.get("recv_name") if isinstance(recv_rec, dict) else recv_rec["recv_name"]
        recv_file = recv_rec.get("recv_file") if isinstance(recv_rec, dict) else recv_rec["recv_file"]
        recv_start_line = recv_rec.get("recv_start_line") if isinstance(recv_rec, dict) else recv_rec["recv_start_line"]
        prop_fqn = recv_rec.get("prop_fqn") if isinstance(recv_rec, dict) else recv_rec["prop_fqn"]
        prop_name = recv_rec.get("prop_name") if isinstance(recv_rec, dict) else recv_rec["prop_name"]
        src_recv_kind = recv_rec.get("src_recv_kind") if isinstance(recv_rec, dict) else recv_rec["src_recv_kind"]
        src_recv_name = recv_rec.get("src_recv_name") if isinstance(recv_rec, dict) else recv_rec["src_recv_name"]
        if recv_kind == "result" and prop_fqn and prop_name:
            member = prop_name.lstrip("$")
            if src_recv_kind == "self" or src_recv_kind is None:
                on = f"$this->{member}"
            elif src_recv_kind == "parameter":
                on = f"{src_recv_name}->{member}"
            elif src_recv_kind == "local":
                on = f"{src_recv_name}->{member}"
            else:
                on = f"$this->{member}"
            return on, "property", prop_fqn, None, None
        elif recv_kind == "self":
            return "$this", "self", None, None, None
        elif recv_kind:
            on_kind = _resolve_on_kind(recv_kind)
            # For local/param receivers, include file:line
            on_file = None
            on_line = None
            if recv_kind in ("local", "parameter"):
                on_file = recv_file
                on_line = recv_start_line
            return recv_name, on_kind, None, on_file, on_line

    # Fallback: no receiver edge. Check call_kind for implicit $this.
    # Matches kloc-cli graph_utils.py: if call_kind in (access, method, method_static) => $this/self
    ck_rec = runner.execute_single(_Q_CALL_KIND, call_id=call_id)
    if ck_rec:
        ck = ck_rec.get("call_kind") if isinstance(ck_rec, dict) else ck_rec["call_kind"]
        if ck in ("access", "method", "method_static"):
            return "$this", "self", None, None, None

    return None, None, None, None, None


# =============================================================================
# build_value_consumer_chain
# =============================================================================


def build_value_consumer_chain(
    runner: QueryRunner,
    value_id: str,
    depth: int,
    max_depth: int,
    limit: int,
    visited: set[str] | None = None,
    crossing_count: int = 0,
    max_crossings: int | None = None,
) -> list[ContextEntry]:
    """Build consumer chain for a Value node (USED BY section).

    Finds all Calls that consume this Value -- either as a receiver (property
    access) or directly as an argument. Groups property accesses by the
    downstream Call that consumes the accessed property result.

    Three-part processing:
      Part 1: Receiver edges -> grouped by consuming Call
      Part 2: Standalone property accesses (not consumed as arguments)
      Part 3: Direct argument edges

    Args:
        runner: Active QueryRunner connected to Neo4j.
        value_id: The Value node ID to find consumers for.
        depth: Current depth level.
        max_depth: Maximum depth to expand.
        limit: Maximum number of entries.
        visited: Set of visited Value IDs for cycle detection.
        crossing_count: Number of return crossings so far in this chain.
        max_crossings: Maximum return crossings allowed per chain.

    Returns:
        List of ContextEntry representing consuming Calls, sorted by (file, line).
    """
    if max_crossings is None:
        max_crossings = min(max_depth, 10)

    if depth > max_depth:
        return []

    if visited is None:
        visited = set()
    if value_id in visited:
        return []
    visited.add(value_id)

    data = fetch_value_consumer_data(runner, value_id)
    receiver_records: list[dict] = data["receiver_chain"]
    direct_arg_records: list[dict] = data["direct_arguments"]
    value_name: str | None = data.get("value_name")
    value_kind_raw: str | None = data.get("value_kind")
    # Resolve on/on_kind from the Value node being queried
    value_on = value_name
    value_on_kind = _resolve_on_kind(value_kind_raw)

    entries: list[ContextEntry] = []
    count = 0
    seen_calls: set[str] = set()

    # === Part 1: Receiver edges grouped by consuming Call ===
    # Group: consumer_call_id -> list of access info dicts
    consumer_groups: dict[str, list[dict]] = {}
    standalone_accesses: list[dict] = []

    for rec in receiver_records:
        access_call_id = rec.get("access_call_id")
        if not access_call_id:
            continue

        consumer_call_id = rec.get("consumer_call_id")
        assigned_local_id = rec.get("assigned_local_id")

        found_consumer = False

        if consumer_call_id:
            # Result is consumed as argument by another Call
            if consumer_call_id not in consumer_groups:
                consumer_groups[consumer_call_id] = []
            consumer_groups[consumer_call_id].append({
                "prop_name": rec.get("target_name", "?"),
                "prop_fqn": rec.get("target_fqn"),
                "position": rec.get("arg_position", 0),
                "expression": rec.get("arg_expression"),
                "access_call_id": access_call_id,
                "access_call_line": rec.get("access_call_line"),
                "consumer_target_id": rec.get("consumer_target_id"),
                "consumer_target_fqn": rec.get("consumer_target_fqn"),
                "consumer_target_name": rec.get("consumer_target_name"),
                "consumer_target_kind": rec.get("consumer_target_kind"),
                "consumer_target_signature": rec.get("consumer_target_signature"),
                "consumer_call_file": rec.get("consumer_call_file"),
                "consumer_call_line": rec.get("consumer_call_line"),
                "consumer_call_kind": rec.get("consumer_call_kind"),
            })
            found_consumer = True

        if not found_consumer and assigned_local_id:
            # Result assigned to a local variable
            found_consumer = True
            standalone_accesses.append(rec)

        if not found_consumer:
            standalone_accesses.append(rec)

    # Build entries for each consuming Call (grouped property accesses)
    for consumer_call_id, access_infos in consumer_groups.items():
        if count >= limit:
            break
        if consumer_call_id in seen_calls:
            continue
        seen_calls.add(consumer_call_id)

        # Use first access info for representative consumer target data
        first = access_infos[0]
        consumer_target_id = first.get("consumer_target_id")
        consumer_target_fqn = first.get("consumer_target_fqn") or ""
        consumer_target_name = first.get("consumer_target_name")
        consumer_target_kind = first.get("consumer_target_kind")
        consumer_target_sig = first.get("consumer_target_signature")
        call_file = first.get("consumer_call_file")
        call_line = first.get("consumer_call_line")
        call_kind = first.get("consumer_call_kind")

        # Build arguments for this consuming Call
        arguments = _build_arguments_from_records(runner, consumer_call_id)

        # Build flat fields
        ref_type = _infer_ref_type_from_call_kind(call_kind, consumer_target_kind)
        callee = _build_callee_display(consumer_target_name, consumer_target_kind)

        # Resolve on/on_kind for the consumer call (Q3 + implicit $this fallback)
        consumer_on, consumer_on_kind, access_chain_symbol, on_file, on_line = _resolve_call_on(runner, consumer_call_id)

        # Build member_ref for correct console rendering order (arrow before tag)
        member_ref = MemberRef(
            target_name=callee or "",
            target_fqn=consumer_target_fqn,
            target_kind=consumer_target_kind,
            file=call_file,
            line=call_line,
            reference_type=ref_type,
            access_chain=consumer_on,
            access_chain_symbol=access_chain_symbol,
            on_kind=consumer_on_kind,
            on_file=on_file,
            on_line=on_line,
        ) if consumer_target_fqn else None

        entry = ContextEntry(
            depth=depth,
            node_id=consumer_target_id or consumer_call_id,
            fqn=consumer_target_fqn,
            kind=consumer_target_kind,
            file=call_file,
            line=call_line,
            signature=consumer_target_sig,
            children=[],
            member_ref=member_ref,
            arguments=arguments,
            ref_type=ref_type,
            callee=callee,
            on=consumer_on,
            on_kind=consumer_on_kind,
        )

        # Depth expansion: cross into callee
        if depth < max_depth and consumer_target_id and consumer_target_kind in ("Method", "Function"):
            cross_into_callee(
                runner, consumer_call_id, consumer_target_id,
                entry, depth, max_depth, limit, visited,
                crossing_count=crossing_count, max_crossings=max_crossings,
            )

        count += 1
        entries.append(entry)

    # === Part 2: Standalone property accesses ===
    for rec in standalone_accesses:
        if count >= limit:
            break
        access_call_id = rec.get("access_call_id")
        if not access_call_id:
            continue
        if access_call_id in seen_calls:
            continue
        seen_calls.add(access_call_id)

        target_id = rec.get("target_id")
        target_fqn = rec.get("target_fqn") or ""
        target_name = rec.get("target_name")
        target_kind = rec.get("target_kind")
        call_file = rec.get("access_call_file")
        call_line = rec.get("access_call_line")
        call_kind = rec.get("access_call_kind")

        ref_type = _infer_ref_type_from_call_kind(call_kind, target_kind)
        callee = _build_callee_display(target_name, target_kind)

        # Build arguments for standalone access call
        arguments = _build_arguments_from_records(runner, access_call_id)

        # Resolve on/on_kind for standalone access (Q3 + implicit $this fallback)
        standalone_on, standalone_on_kind, access_chain_symbol, on_file, on_line = _resolve_call_on(runner, access_call_id)
        # Fall back to value-level on/on_kind if Q3 doesn't resolve
        if standalone_on is None:
            standalone_on = value_on
            standalone_on_kind = value_on_kind

        # Build member_ref for correct console rendering order (arrow before tag)
        member_ref = MemberRef(
            target_name=callee or "",
            target_fqn=target_fqn,
            target_kind=target_kind,
            file=call_file,
            line=call_line,
            reference_type=ref_type,
            access_chain=standalone_on,
            access_chain_symbol=access_chain_symbol,
            on_kind=standalone_on_kind,
            on_file=on_file,
            on_line=on_line,
        ) if target_fqn else None

        entry = ContextEntry(
            depth=depth,
            node_id=target_id or access_call_id,
            fqn=target_fqn,
            kind=target_kind,
            file=call_file,
            line=call_line,
            children=[],
            member_ref=member_ref,
            arguments=arguments,
            ref_type=ref_type,
            callee=callee,
            on=standalone_on,
            on_kind=standalone_on_kind,
        )
        count += 1
        entries.append(entry)

    # === Part 3: Direct argument edges ===
    for rec in direct_arg_records:
        if count >= limit:
            break
        consumer_call_id = rec.get("consumer_call_id")
        if not consumer_call_id:
            continue
        if consumer_call_id in seen_calls:
            continue
        seen_calls.add(consumer_call_id)

        consumer_target_id = rec.get("consumer_target_id")
        consumer_target_fqn = rec.get("consumer_target_fqn") or ""
        consumer_target_name = rec.get("consumer_target_name")
        consumer_target_kind = rec.get("consumer_target_kind")
        consumer_target_sig = rec.get("consumer_target_signature")
        call_file = rec.get("consumer_call_file")
        call_line = rec.get("consumer_call_line")
        call_kind = rec.get("consumer_call_kind")

        # Build arguments
        arguments = _build_arguments_from_records(runner, consumer_call_id)

        ref_type = _infer_ref_type_from_call_kind(call_kind, consumer_target_kind)
        callee = _build_callee_display(consumer_target_name, consumer_target_kind)

        # Resolve on/on_kind for direct argument consumer calls (Q3 + implicit $this fallback)
        direct_on, direct_on_kind, access_chain_symbol, on_file, on_line = _resolve_call_on(runner, consumer_call_id)

        # Build member_ref for correct console rendering order (arrow before tag)
        member_ref = MemberRef(
            target_name=callee or "",
            target_fqn=consumer_target_fqn,
            target_kind=consumer_target_kind,
            file=call_file,
            line=call_line,
            reference_type=ref_type,
            access_chain=direct_on,
            access_chain_symbol=access_chain_symbol,
            on_kind=direct_on_kind,
            on_file=on_file,
            on_line=on_line,
        ) if consumer_target_fqn else None

        entry = ContextEntry(
            depth=depth,
            node_id=consumer_target_id or consumer_call_id,
            fqn=consumer_target_fqn,
            kind=consumer_target_kind,
            file=call_file,
            line=call_line,
            signature=consumer_target_sig,
            children=[],
            member_ref=member_ref,
            arguments=arguments,
            ref_type=ref_type,
            callee=callee,
            on=direct_on,
            on_kind=direct_on_kind,
        )

        # Cross into callee for direct arguments too
        if depth < max_depth and consumer_target_id and consumer_target_kind in ("Method", "Function"):
            cross_into_callee(
                runner, consumer_call_id, consumer_target_id,
                entry, depth, max_depth, limit, visited,
                crossing_count=crossing_count, max_crossings=max_crossings,
            )

        count += 1
        entries.append(entry)

    # Sort all entries by (file, line)
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

    return entries


# =============================================================================
# cross_into_callee
# =============================================================================


def cross_into_callee(
    runner: QueryRunner,
    call_node_id: str,
    callee_id: str,
    entry: ContextEntry,
    depth: int,
    max_depth: int,
    limit: int,
    visited: set[str],
    crossing_count: int = 0,
    max_crossings: int | None = None,
) -> None:
    """Cross method boundary from caller into callee for USED BY tracing.

    For each argument edge with a parameter FQN, finds the matching
    Value(parameter) node in the callee and recursively traces its consumers.

    Also follows the return value path: if the call produces a result
    assigned to a local variable, traces that local's consumers.

    Args:
        runner: Active QueryRunner.
        call_node_id: The Call node in the caller.
        callee_id: The callee Method/Function node ID.
        entry: The ContextEntry to attach children to.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited Value IDs for cycle detection.
        crossing_count: Number of return crossings so far.
        max_crossings: Maximum return crossings allowed.
    """
    if max_crossings is None:
        max_crossings = min(max_depth, 10)

    # Cross into callee via argument parameter FQNs
    arg_records = runner.execute(Q4_ARGUMENT_PARAMS, call_id=call_node_id)
    for rec in arg_records:
        parameter_fqn = rec.get("parameter_fqn")
        if not parameter_fqn:
            continue

        # Resolve parameter FQN to Value(parameter) node
        param_rec = runner.execute_single(Q5_RESOLVE_PARAM, param_fqn=parameter_fqn)
        if not param_rec:
            continue
        param_value_id = param_rec.get("value_id") if isinstance(param_rec, dict) else param_rec["value_id"]
        if not param_value_id or param_value_id in visited:
            continue

        child_entries = build_value_consumer_chain(
            runner, param_value_id, depth + 1, max_depth, limit, visited,
            crossing_count=crossing_count, max_crossings=max_crossings,
        )
        for ce in child_entries:
            if not ce.crossed_from:
                ce.crossed_from = parameter_fqn
        entry.children.extend(child_entries)

    # Return value path: if callee return flows back to a local in caller
    local_rec = runner.execute_single(Q6_LOCAL_FOR_RESULT, call_id=call_node_id)
    local_id = None
    if local_rec:
        local_id = local_rec.get("local_id") if isinstance(local_rec, dict) else local_rec["local_id"]

    if local_id and local_id not in visited:
        return_entries = build_value_consumer_chain(
            runner, local_id, depth + 1, max_depth, limit, visited,
            crossing_count=crossing_count, max_crossings=max_crossings,
        )
        entry.children.extend(return_entries)
    elif not local_id:
        # No local assignment -- check for return expression crossing
        cross_into_callers_via_return(
            runner, call_node_id, entry, depth, max_depth, limit, visited,
            crossing_count=crossing_count, max_crossings=max_crossings,
        )


# =============================================================================
# cross_into_callers_via_return
# =============================================================================


def cross_into_callers_via_return(
    runner: QueryRunner,
    call_node_id: str,
    entry: ContextEntry,
    depth: int,
    max_depth: int,
    limit: int,
    visited: set[str],
    crossing_count: int = 0,
    max_crossings: int | None = None,
) -> None:
    """Cross from callee return back into caller scope via return value.

    When a Value is consumed by a Call that is the return expression of a method,
    and there is no local variable assigned from the result, this traces the
    return value into caller scopes where the result IS assigned to a local.

    Safety guards:
    - Depth budget: depth >= max_depth -> STOP
    - Crossing limit: crossing_count >= max_crossings -> STOP
    - Method-level cycle prevention via visited set
    - Fan-out cap: callers capped at limit
    - Type guard: consumer result type must match caller local type

    Args:
        runner: Active QueryRunner.
        call_node_id: The consumer Call node ID.
        entry: The ContextEntry to attach children to.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited Value/method-crossing IDs.
        crossing_count: Number of return crossings so far.
        max_crossings: Maximum return crossings allowed.
    """
    if max_crossings is None:
        max_crossings = min(max_depth, 10)

    # Guard 1: Depth budget
    if depth >= max_depth:
        return

    # Guard 2: Crossing limit
    if crossing_count >= max_crossings:
        return

    # Step 1: Get produced Value(result) from consumer Call
    local_rec = runner.execute_single(Q6_LOCAL_FOR_RESULT, call_id=call_node_id)
    if not local_rec:
        return

    result_id = local_rec.get("result_id") if isinstance(local_rec, dict) else local_rec["result_id"]
    if not result_id:
        return

    # Step 2: Verify no local assignment (inline return check)
    check_local = local_rec.get("local_id") if isinstance(local_rec, dict) else local_rec["local_id"]
    if check_local:
        return  # Has a local assignment, not an inline return

    # Step 3: TYPE GUARD -- get consumer result type_of
    type_rec = runner.execute_single(Q7_TYPE_OF, value_id=result_id)
    if not type_rec:
        return  # No type info, don't cross (conservative)
    consumer_type_id = type_rec.get("type_id") if isinstance(type_rec, dict) else type_rec["type_id"]
    if not consumer_type_id:
        return

    # Step 4: Find containing method of the consumer Call
    method_rec = runner.execute_single(Q12_CONTAINING_METHOD, node_id=call_node_id)
    if not method_rec:
        return
    containing_method_id = method_rec.get("method_id") if isinstance(method_rec, dict) else method_rec["method_id"]
    if not containing_method_id:
        return

    # Guard 3: Method-level cycle prevention
    method_key = f"return_crossing:{containing_method_id}"
    if method_key in visited:
        return

    # Step 5: Find callers of the containing method
    caller_records = runner.execute(Q8_METHOD_CALLERS, method_id=containing_method_id)

    # Guard 4: Fan-out cap
    for rec in caller_records[:limit]:
        caller_local_id = rec.get("caller_local_id")
        if not caller_local_id or caller_local_id in visited:
            continue

        # Guard 5: Type matching
        caller_type_id = rec.get("caller_type_id")
        if caller_type_id != consumer_type_id:
            continue

        # Mark method as visited NOW (lazy marking after type match)
        visited.add(method_key)

        # Continue tracing from caller's local (increment crossing_count)
        return_entries = build_value_consumer_chain(
            runner, caller_local_id, depth + 1, max_depth, limit, visited,
            crossing_count=crossing_count + 1, max_crossings=max_crossings,
        )

        # Set crossed_from to show caller method FQN
        caller_method_fqn = rec.get("caller_method_fqn")
        if caller_method_fqn:
            for child_entry in return_entries:
                if not child_entry.crossed_from:
                    child_entry.crossed_from = caller_method_fqn

        entry.children.extend(return_entries)


# =============================================================================
# build_value_source_chain
# =============================================================================


def build_value_source_chain(
    runner: QueryRunner,
    value_id: str,
    depth: int,
    max_depth: int,
    limit: int,
    visited: set[str] | None = None,
) -> list[ContextEntry]:
    """Build source chain for a Value node (USES section).

    Traces backwards: $savedOrder <- save($processedOrder) <- process($order) <- new Order(...)
    Each depth level follows assigned_from -> produces -> Call, then recursively
    traces the Call's argument Values' source chains.

    For parameter Values: crosses method boundary to find callers via argument
    edges with matching parameter FQN.

    Args:
        runner: Active QueryRunner.
        value_id: ID of the Value node to trace from.
        depth: Current depth level.
        max_depth: Maximum depth for recursion.
        limit: Maximum number of entries.
        visited: Set of visited Value IDs for cycle detection.

    Returns:
        List of ContextEntry instances representing the source chain.
    """
    if depth > max_depth:
        return []

    if visited is None:
        visited = set()
    if value_id in visited:
        return []
    visited.add(value_id)

    # Fetch source chain data
    source_data = fetch_value_source_data(runner, value_id)
    if not source_data:
        return []

    value_kind = source_data.get("value_kind")
    value_fqn = source_data.get("value_fqn")

    # Parameter Values: cross method boundary to find callers
    if value_kind == "parameter":
        return build_parameter_uses(
            runner, value_id, value_fqn or "", depth, max_depth, limit, visited
        )

    # Follow assigned_from to source
    source_id = source_data.get("source_id")

    # If no assigned_from and value is result: result IS the source
    if not source_id and value_kind == "result":
        source_id = value_id

    if not source_id:
        return []

    # Find the Call that produced the source Value
    call_id = source_data.get("call_id")
    if not call_id:
        return []

    # Get call target (callee method/constructor)
    callee_id = source_data.get("callee_id")
    callee_fqn = source_data.get("callee_fqn") or ""
    callee_name = source_data.get("callee_name")
    callee_kind = source_data.get("callee_kind")
    callee_signature = source_data.get("callee_signature")
    call_file = source_data.get("call_file")
    call_line = source_data.get("call_line")
    call_kind_val = source_data.get("call_kind")

    if not callee_id:
        return []

    # Build reference type
    ref_type = _infer_ref_type_from_call_kind(call_kind_val, callee_kind)

    # Build receiver info for access chain
    recv_value_kind = source_data.get("recv_value_kind")
    recv_name = source_data.get("recv_name")
    recv_prop_fqn = source_data.get("recv_prop_fqn")
    recv_prop_name = source_data.get("recv_prop_name")

    access_chain = recv_name
    access_chain_symbol = None
    on_kind = _resolve_on_kind(recv_value_kind)
    if recv_prop_fqn:
        # Build expression like "$this->propName" instead of FQN
        if recv_prop_name:
            member = recv_prop_name.lstrip("$")
        elif "::" in recv_prop_fqn:
            member = recv_prop_fqn.rsplit("::", 1)[-1].lstrip("$")
        else:
            member = recv_prop_fqn
        access_chain = f"$this->{member}"
        access_chain_symbol = recv_prop_fqn
        on_kind = "property"

    # Build member ref
    member_ref = MemberRef(
        target_name=_build_callee_display(callee_name, callee_kind) or "",
        target_fqn=callee_fqn,
        target_kind=callee_kind,
        file=call_file,
        line=call_line,
        reference_type=ref_type,
        access_chain=access_chain,
        access_chain_symbol=access_chain_symbol,
        on_kind=on_kind,
    )

    # Build arguments
    arguments = _build_arguments_from_records(runner, call_id)

    entry = ContextEntry(
        depth=depth,
        node_id=callee_id,
        fqn=callee_fqn,
        kind=callee_kind,
        file=call_file,
        line=call_line,
        signature=callee_signature,
        children=[],
        member_ref=member_ref,
        arguments=arguments,
        entry_type="call",
    )

    # Recursively trace each argument's source chain at depth+1
    if depth < max_depth:
        for arg in arguments:
            if arg.value_ref_symbol:
                # Find Value node by FQN and trace its source
                # Try parameter first, then any Value
                param_rec = runner.execute_single(Q5_RESOLVE_PARAM, param_fqn=arg.value_ref_symbol)
                if not param_rec:
                    # Fallback: resolve any Value by FQN (handles locals)
                    param_rec = runner.execute_single(
                        "MATCH (v:Value {fqn: $param_fqn}) RETURN v.node_id AS value_id LIMIT 1",
                        param_fqn=arg.value_ref_symbol,
                    )
                if param_rec:
                    arg_val_id = param_rec.get("value_id") if isinstance(param_rec, dict) else param_rec["value_id"]
                    if arg_val_id and arg_val_id not in visited:
                        children = build_value_source_chain(
                            runner, arg_val_id, depth + 1, max_depth, limit, visited
                        )
                        entry.children.extend(children)
            elif arg.source_chain:
                # Result argument (e.g., $input->customerEmail) — trace the
                # receiver Value to follow data flow across method boundaries
                for step in arg.source_chain:
                    on_fqn = step.get("on")
                    if on_fqn:
                        on_rec = runner.execute_single(
                            "MATCH (v:Value {fqn: $fqn}) RETURN v.node_id AS value_id LIMIT 1",
                            fqn=on_fqn,
                        )
                        if on_rec:
                            on_val_id = on_rec.get("value_id") if isinstance(on_rec, dict) else on_rec["value_id"]
                            if on_val_id and on_val_id not in visited:
                                children = build_value_source_chain(
                                    runner, on_val_id, depth + 1, max_depth, limit, visited
                                )
                                entry.children.extend(children)

    return [entry]


# =============================================================================
# build_parameter_uses
# =============================================================================


def build_parameter_uses(
    runner: QueryRunner,
    param_value_id: str,
    param_fqn: str,
    depth: int,
    max_depth: int,
    limit: int,
    visited: set[str],
) -> list[ContextEntry]:
    """Find callers of a parameter Value via argument edges matching parameter FQN.

    Searches all argument edges where the `parameter` field matches this Value's
    FQN, then traces the source of each caller's argument Value.

    Args:
        runner: Active QueryRunner.
        param_value_id: ID of the parameter Value node.
        param_fqn: FQN of the parameter Value.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited Value IDs for cycle detection.

    Returns:
        List of ContextEntry representing caller-provided sources.
    """
    records = runner.execute(Q10_PARAMETER_USES, param_fqn=param_fqn)
    entries: list[ContextEntry] = []

    for rec in records:
        if len(entries) >= limit:
            break

        call_id = rec.get("call_id")
        if not call_id:
            continue

        scope_id = rec.get("scope_id")
        scope_fqn = rec.get("scope_fqn") or ""
        scope_kind = rec.get("scope_kind")
        scope_signature = rec.get("scope_signature")
        call_file = rec.get("call_file")
        call_line = rec.get("call_line")
        caller_value_id = rec.get("value_id")

        entry = ContextEntry(
            depth=depth,
            node_id=scope_id or call_id,
            fqn=scope_fqn,
            kind=scope_kind,
            file=call_file,
            line=call_line,
            signature=scope_signature,
            children=[],
            crossed_from=param_fqn,
        )

        # Trace the caller's argument Value's source chain at depth+1
        if depth < max_depth and caller_value_id and caller_value_id not in visited:
            child_entries = build_value_source_chain(
                runner, caller_value_id, depth + 1, max_depth, limit, visited
            )
            entry.children.extend(child_entries)

        entries.append(entry)

    # Sort by (file, line)
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries
