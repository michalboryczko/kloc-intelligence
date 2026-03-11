"""Class context orchestrators: build USED BY and USES trees for Class nodes.

Ported from kloc-cli's class context logic. Each public function fetches
pre-resolved data from Neo4j (via batch query functions) then applies pure
logic (handlers, reference type inference, graph helpers) to produce lists
of ContextEntry objects.

Key design rules:
- All Neo4j access is via batch fetch functions — no per-entry round-trips.
- Handlers produce dicts; dicts are converted to ContextEntry at the end.
- Extends entries from Q1 are built directly, bypassing the handler pipeline.
- Injection suppression: method_call from a class with property_type injection
  is suppressed at depth 1 (MethodCallHandler already does this).
- All line numbers are stored 0-based; conversion to 1-based happens in OutputEntry.
"""

from ..db.query_runner import QueryRunner
from ..db.queries.context_class import (
    Q5_CALLER_CHAIN,
    Q7_INJECTION_POINT_CALLS,
    fetch_class_used_by_data,
)
from ..db.queries.context_class_uses import (
    Q3_BEHAVIORAL_DEPTH2,
    fetch_class_uses_data,
)
from ..logic.handlers import EdgeContext, EntryBucket, USED_BY_HANDLERS
from ..logic.reference_types import (
    infer_reference_type,
    CHAINABLE_REFERENCE_TYPES,
)
from ..logic.graph_helpers import format_method_fqn
from ..models.node import NodeData
from ..models.results import ContextEntry, ArgumentInfo
from .value_context import resolve_promoted_property_fqn


# Query to resolve access chain from a Call node's receiver
_Q_RESOLVE_ACCESS_CHAIN = """
MATCH (call:Node {node_id: $call_id})-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(src_call:Call)-[:CALLS]->(prop)
OPTIONAL MATCH (src_call)-[:RECEIVER]->(src_recv:Value)
RETURN recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       src_call.call_kind AS src_call_kind,
       prop.fqn AS prop_fqn,
       prop.name AS prop_name,
       prop.kind AS prop_kind,
       src_recv.value_kind AS src_recv_value_kind,
       src_recv.name AS src_recv_name
LIMIT 1
"""

# Query to get argument info for a Call
_Q_CALL_ARGUMENTS = """
MATCH (call:Node {node_id: $call_id})-[a:ARGUMENT]->(val:Value)
OPTIONAL MATCH (val)-[:TYPE_OF]->(vtype)
RETURN a.position AS position,
       a.expression AS expression,
       a.parameter AS parameter,
       val.node_id AS value_node_id,
       val.value_kind AS value_kind,
       val.name AS value_name,
       val.fqn AS value_fqn,
       vtype.name AS value_type
ORDER BY a.position
"""

# Query to trace source chain for a result Value
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

# Query to find override methods in a subclass for extends expansion
_Q_OVERRIDE_METHODS = """
MATCH (subclass:Node {node_id: $subclass_id})-[:CONTAINS]->(method:Method)
WHERE method.name <> '__construct'
MATCH (method)-[:OVERRIDES]->(parent_method:Method)
RETURN DISTINCT method.node_id AS method_id,
       method.fqn AS method_fqn,
       method.file AS method_file,
       method.start_line AS method_start_line,
       method.signature AS method_signature
ORDER BY method.file, method.start_line
"""

# Priority order for USES entries (matches kloc-cli's sort priority)
USES_PRIORITY: dict[str, int] = {
    "extends": 0,
    "implements": 0,
    "uses_trait": 0,
    "property_type": 1,
    "parameter_type": 2,
    "return_type": 2,
    "instantiation": 3,
    "type_hint": 4,
    "method_call": 5,
    "property_access": 5,
}


# =============================================================================
# Internal helpers
# =============================================================================


def _resolve_call_access_chain(
    runner: "QueryRunner", call_id: str
) -> tuple[str | None, str | None]:
    """Resolve access chain expression and symbol from a Call node's receiver.

    Returns (access_chain, access_chain_symbol) e.g.:
    - ("$this->orderService", "App\\Service\\OrderService::$orderService")
    - ("$param", None) for parameter receivers
    """
    rec = runner.execute_single(_Q_RESOLVE_ACCESS_CHAIN, call_id=call_id)
    if not rec:
        return None, None

    recv_value_kind = rec.get("recv_value_kind")
    recv_name = rec.get("recv_name")
    prop_fqn = rec.get("prop_fqn")
    prop_name = rec.get("prop_name")
    src_call_kind = rec.get("src_call_kind")
    src_recv_value_kind = rec.get("src_recv_value_kind")
    src_recv_name = rec.get("src_recv_name")

    if recv_value_kind is None:
        return None, None

    if recv_value_kind == "parameter":
        return recv_name, None

    if recv_value_kind == "local":
        return recv_name, None

    if recv_value_kind == "self":
        return "$this", None

    if recv_value_kind == "result":
        if src_call_kind == "access" and prop_name:
            member = prop_name.lstrip("$")
            if src_recv_value_kind == "self" or src_recv_value_kind is None:
                chain = f"$this->{member}"
            elif src_recv_value_kind == "parameter":
                chain = f"{src_recv_name}->{member}"
            elif src_recv_value_kind == "local":
                chain = f"{src_recv_name}->{member}"
            else:
                chain = f"$this->{member}"
            return chain, prop_fqn
        if src_call_kind in ("method", "method_static") and prop_name:
            member = prop_name
            if src_recv_value_kind == "self" or src_recv_value_kind is None:
                chain = f"$this->{member}()"
            elif src_recv_value_kind == "parameter":
                chain = f"{src_recv_name}->{member}()"
            elif src_recv_value_kind == "local":
                chain = f"{src_recv_name}->{member}()"
            else:
                chain = f"$this->{member}()"
            return chain, prop_fqn

    return None, None


def _trace_source_chain(runner: "QueryRunner", value_node_id: str) -> list | None:
    """Trace the source chain for a result Value node.

    For property access results, follows the receiver chain to build
    a source chain showing what property is accessed on what object.

    Args:
        runner: Active QueryRunner.
        value_node_id: ID of the result Value node.

    Returns:
        List of chain step dicts, or None if chain cannot be traced.
    """
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


def _build_call_arguments(runner: "QueryRunner", call_id: str) -> list[ArgumentInfo]:
    """Build ArgumentInfo list for a Call by querying argument edges."""
    records = runner.execute(_Q_CALL_ARGUMENTS, call_id=call_id)
    infos: list[ArgumentInfo] = []
    for r in records:
        position = r.get("position")
        if position is None:
            continue
        raw_param_fqn = r.get("parameter")
        # Resolve promoted constructor params to Property FQNs
        param_fqn = raw_param_fqn
        if raw_param_fqn:
            resolved = resolve_promoted_property_fqn(runner, raw_param_fqn)
            if resolved:
                param_fqn = resolved
        param_name = None
        if param_fqn:
            if ".$" in param_fqn:
                param_name = param_fqn.rsplit(".", 1)[-1]
            elif "$" in param_fqn:
                param_name = "$" + param_fqn.rsplit("$", 1)[-1]
        value_fqn = r.get("value_fqn")
        value_ref_symbol = None
        value_kind = r.get("value_kind")
        if value_fqn and "::" in value_fqn:
            value_ref_symbol = value_fqn

        # Trace source chain for result values
        source_chain = None
        if value_kind == "result":
            value_node_id = r.get("value_node_id")
            if value_node_id:
                source_chain = _trace_source_chain(runner, value_node_id)

        infos.append(ArgumentInfo(
            position=int(position),
            param_name=param_name,
            param_fqn=param_fqn,
            value_expr=r.get("expression"),
            value_source=value_kind,
            value_type=r.get("value_type"),
            value_ref_symbol=value_ref_symbol,
            source_chain=source_chain,
        ))
    infos.sort(key=lambda a: a.position)
    return infos


def _build_on_from_receiver(
    recv_value_kind: str | None,
    recv_name: str | None,
    recv_prop_fqn: str | None,
    recv_prop_name: str | None,
    src_recv_value_kind: str | None,
    src_recv_name: str | None,
) -> tuple[str | None, str | None]:
    """Build on expression and on_kind from receiver chain data.

    Returns:
        (on_expr, on_kind) tuple.
    """
    if recv_value_kind is None:
        return None, None

    if recv_value_kind == "parameter":
        return recv_name, "param"

    if recv_value_kind == "local":
        return recv_name, "local"

    if recv_value_kind == "self":
        return "$this", "self"

    if recv_value_kind == "result":
        if recv_prop_name:
            member = recv_prop_name.lstrip("$")
            if src_recv_value_kind == "self" or src_recv_value_kind is None:
                return f"$this->{member}", "property"
            elif src_recv_value_kind == "parameter":
                return f"{src_recv_name}->{member}", "property"
            elif src_recv_value_kind == "local":
                return f"{src_recv_name}->{member}", "property"
            return f"$this->{member}", "property"
        elif recv_prop_fqn:
            if "::" in recv_prop_fqn:
                member = recv_prop_fqn.rsplit("::", 1)[-1].lstrip("$")
            else:
                member = recv_prop_fqn
            return f"$this->{member}", "property"

    return None, None


def _dict_to_context_entry(d: dict, depth: int | None = None) -> ContextEntry:
    """Convert a handler-produced dict to a ContextEntry.

    Handler dicts use snake_case keys and may omit optional fields.
    ContextEntry has sensible defaults for all optional fields.

    Args:
        d: Dict produced by a USED_BY handler or directly constructed.
        depth: Override depth (uses d["depth"] if None).

    Returns:
        ContextEntry populated from dict.
    """
    # Convert arguments from handler dicts — may be ArgumentInfo objects or dicts
    raw_args = d.get("arguments", [])
    arguments = []
    for a in raw_args:
        if isinstance(a, ArgumentInfo):
            arguments.append(a)
        elif isinstance(a, dict):
            arguments.append(ArgumentInfo(**a))
    return ContextEntry(
        depth=depth if depth is not None else d.get("depth", 1),
        node_id=d.get("node_id", ""),
        fqn=d.get("fqn", ""),
        kind=d.get("kind"),
        file=d.get("file"),
        line=d.get("line"),
        signature=d.get("signature"),
        ref_type=d.get("ref_type"),
        callee=d.get("callee"),
        on=d.get("on"),
        on_kind=d.get("on_kind"),
        sites=d.get("sites"),
        property_name=d.get("property_name"),
        access_count=d.get("access_count"),
        method_count=d.get("method_count"),
        children=d.get("children", []),
        arguments=arguments,
    )


def _build_property_access_entries(
    bucket: EntryBucket,
    target_fqn: str,
    depth: int,
) -> list[ContextEntry]:
    """Convert property_access_groups in bucket to PropertyGroup ContextEntry objects.

    Groups all accesses to the same property across all methods into a single
    PropertyGroup entry with aggregated accessCount and methodCount.

    At depth 1: shows the PropertyGroup (no per-method breakdown).
    Children (per-method breakdown) are added at depth 2+ by the caller.

    Args:
        bucket: EntryBucket with populated property_access_groups.
        target_fqn: FQN of the target class, used to build short display FQN.
        depth: Depth to assign to generated entries.

    Returns:
        List of ContextEntry objects, one per property.
    """
    entries: list[ContextEntry] = []
    short_class = target_fqn.rsplit("\\", 1)[-1] if "\\" in target_fqn else target_fqn
    for prop_fqn, groups in bucket.property_access_groups.items():
        total_accesses = sum(len(g["lines"]) for g in groups)
        total_methods = len(groups)

        # Build short display FQN: "Order::$createdAt"
        if "::" in prop_fqn:
            prop_short = prop_fqn.rsplit("::", 1)[-1]
        else:
            prop_short = prop_fqn
        display_fqn = f"{short_class}::{prop_short}"

        entry = ContextEntry(
            depth=depth,
            node_id=prop_fqn,  # Use prop_fqn as node_id for dedup
            fqn=display_fqn,
            kind="PropertyGroup",
            file=None,
            line=None,
            ref_type="property_access",
            access_count=total_accesses,
            method_count=total_methods,
        )
        entries.append(entry)
    return entries


def _build_caller_chain_entry(
    runner: QueryRunner, record: dict, depth: int, crossed_from_fqn: str | None = None,
) -> ContextEntry:
    """Build a single ContextEntry from a Q5 caller chain record.

    Caller chain entries represent "who calls this method", not call sites.
    They use the caller's definition line (not call site line) and do not
    include arguments.

    Args:
        runner: Active QueryRunner.
        record: Q5 query result record.
        depth: Current depth level.
        crossed_from_fqn: FQN of the method being expanded (the callee).
    """
    fqn = format_method_fqn(record["caller_fqn"], record.get("caller_kind", ""))
    return ContextEntry(
        depth=depth,
        node_id=record["caller_id"],
        fqn=fqn,
        kind=record.get("caller_kind"),
        file=record.get("caller_file"),
        line=record.get("caller_start_line"),
        ref_type="caller",
        crossed_from=crossed_from_fqn,
    )


# =============================================================================
# build_caller_chain_for_method
# =============================================================================


_Q_METHOD_FQN = """
MATCH (m:Node {node_id: $method_id})
WHERE m.kind IN ['Method', 'Function']
RETURN m.fqn AS fqn, m.kind AS kind
"""

# Find override root(s): follow OVERRIDES chain to find the topmost
# abstract/interface method that this method implements.
_Q_OVERRIDE_ROOTS = """
MATCH (m:Node {node_id: $method_id})-[:OVERRIDES*1..5]->(root:Node)
WHERE root.kind = 'Method'
  AND NOT EXISTS { MATCH (root)-[:OVERRIDES]->(:Node) }
RETURN root.node_id AS root_id
"""


def build_caller_chain_for_method(
    runner: QueryRunner,
    method_id: str,
    depth: int,
    max_depth: int,
    visited: set[str],
) -> list[ContextEntry]:
    """Recursively fetch callers of a method up to max_depth.

    Uses override root resolution: callers may reference the interface/abstract
    method rather than the concrete implementation, so we check both.

    Args:
        runner: Active QueryRunner.
        method_id: Node ID of the method to find callers for.
        depth: Current depth in the expansion (starts at 2).
        max_depth: Maximum depth allowed.
        visited: Set of already-visited method node IDs (mutated in place).

    Returns:
        List of ContextEntry objects for callers, with recursive children.
    """
    if depth > max_depth:
        return []

    # Resolve the method's FQN for crossed_from
    method_rec = runner.execute_single(_Q_METHOD_FQN, method_id=method_id)
    crossed_from_fqn = None
    if method_rec:
        crossed_from_fqn = method_rec.get("fqn")
        method_kind = method_rec.get("kind")
        if crossed_from_fqn and method_kind == "Method" and not crossed_from_fqn.endswith("()"):
            crossed_from_fqn += "()"

    # Collect callers from the method itself
    records = list(runner.execute(Q5_CALLER_CHAIN, method_id=method_id))

    # Also collect callers from override roots (interface/abstract methods)
    override_roots = runner.execute(_Q_OVERRIDE_ROOTS, method_id=method_id)
    seen_caller_ids: set[str] = {r["caller_id"] for r in records}
    for root_rec in override_roots:
        root_id = root_rec.get("root_id")
        if root_id and root_id != method_id:
            root_records = runner.execute(Q5_CALLER_CHAIN, method_id=root_id)
            for rr in root_records:
                if rr["caller_id"] not in seen_caller_ids:
                    seen_caller_ids.add(rr["caller_id"])
                    records.append(rr)

    # Also check direct OVERRIDES targets (not just roots)
    direct_overrides = runner.execute(
        "MATCH (m:Node {node_id: $method_id})-[:OVERRIDES]->(parent:Node) "
        "WHERE parent.kind = 'Method' RETURN parent.node_id AS parent_id",
        method_id=method_id,
    )
    for ov_rec in direct_overrides:
        parent_id = ov_rec.get("parent_id")
        if parent_id and parent_id != method_id:
            parent_records = runner.execute(Q5_CALLER_CHAIN, method_id=parent_id)
            for pr in parent_records:
                if pr["caller_id"] not in seen_caller_ids:
                    seen_caller_ids.add(pr["caller_id"])
                    records.append(pr)

    entries: list[ContextEntry] = []

    for r in records:
        caller_id = r["caller_id"]
        if caller_id in visited:
            continue
        visited.add(caller_id)

        entry = _build_caller_chain_entry(runner, dict(r), depth, crossed_from_fqn)

        # Recurse if within depth and caller is chainable
        if depth < max_depth and entry.ref_type in CHAINABLE_REFERENCE_TYPES:
            entry.children = build_caller_chain_for_method(
                runner, caller_id, depth + 1, max_depth, visited
            )

        entries.append(entry)

    # Sort by (file, line) like kloc-cli
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


# =============================================================================
# build_class_used_by_depth_callers
# =============================================================================

# Resolve receiver identity for on/onKind display in depth callers
_Q_CALL_RECEIVER_FOR_DEPTH = """
MATCH (call:Node {node_id: $call_id})
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(src_call:Call)-[:CALLS]->(src_prop)
OPTIONAL MATCH (src_call)-[:RECEIVER]->(src_recv:Value)
RETURN recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       src_prop.fqn AS src_prop_fqn,
       src_prop.name AS src_prop_name,
       call.call_kind AS call_kind,
       src_recv.value_kind AS src_recv_value_kind,
       src_recv.name AS src_recv_name
"""


def build_class_used_by_depth_callers(
    runner: QueryRunner,
    method_id: str,
    depth: int,
    max_depth: int,
    visited: set[str],
) -> list[ContextEntry]:
    """Find callers of a method for depth expansion in class USED BY.

    Unlike build_caller_chain_for_method (which produces "caller" entries for
    property context), this produces rich method_call entries with callee,
    on, on_kind, and arguments — used for class USED BY depth-2+ expansion.

    At depth 2: refType = "method_call" with full call details.
    At depth 3+: refType = "caller" (simpler entries).

    Args:
        runner: Active QueryRunner.
        method_id: Node ID of the method to find callers for.
        depth: Current depth (starts at 2).
        max_depth: Maximum depth allowed.
        visited: Set of visited method IDs for cycle detection.

    Returns:
        List of ContextEntry objects.
    """
    if depth > max_depth:
        return []

    # Resolve the callee method's FQN for crossed_from and callee display
    method_rec = runner.execute_single(_Q_METHOD_FQN, method_id=method_id)
    if not method_rec:
        return []

    callee_fqn = method_rec.get("fqn") or ""
    callee_kind = method_rec.get("kind") or ""
    if callee_kind == "Method" and not callee_fqn.endswith("()"):
        crossed_from_fqn = callee_fqn + "()"
    else:
        crossed_from_fqn = callee_fqn

    callee_name_raw = callee_fqn.rsplit("::", 1)[-1] if "::" in callee_fqn else callee_fqn
    callee_display = callee_name_raw + "()" if callee_kind == "Method" and not callee_name_raw.endswith("()") else callee_name_raw

    # Collect direct callers only (no override parent traversal, matching kloc-cli)
    records = list(runner.execute(Q5_CALLER_CHAIN, method_id=method_id))

    entries: list[ContextEntry] = []

    for r in records:
        caller_id = r["caller_id"]
        if caller_id in visited:
            continue
        visited.add(caller_id)

        caller_fqn = format_method_fqn(r["caller_fqn"], r.get("caller_kind", ""))
        call_id = r.get("call_id")
        call_line = r.get("call_line")

        # Resolve the USES edge location line (kloc-cli uses edge.location.line, not call.start_line)
        uses_edge = runner.execute_single(
            "MATCH (src:Node {node_id: $caller_id})-[e:USES]->(tgt:Node {node_id: $method_id}) "
            "RETURN e.loc_line AS loc_line, e.loc_file AS loc_file",
            caller_id=caller_id, method_id=method_id,
        )
        if uses_edge and uses_edge.get("loc_line") is not None:
            call_line = uses_edge["loc_line"]

        # Determine refType based on depth
        if depth >= 3:
            entry_ref_type = "caller"
        else:
            entry_ref_type = "method_call"

        # Build on/onKind from receiver (always resolve, not just at depth 2)
        on_expr = None
        on_kind = None
        arguments: list[ArgumentInfo] = []

        if call_id:
            # Resolve receiver for on/onKind
            recv_rec = runner.execute_single(
                _Q_CALL_RECEIVER_FOR_DEPTH, call_id=call_id
            )
            if recv_rec:
                recv_kind = recv_rec.get("recv_value_kind")
                recv_name = recv_rec.get("recv_name")
                src_prop_fqn = recv_rec.get("src_prop_fqn")
                src_prop_name = recv_rec.get("src_prop_name")
                call_kind_val = recv_rec.get("call_kind")

                if recv_kind == "self" or (recv_kind is None and call_kind_val in ("access", "method", "method_static")):
                    # Self access: $this->prop
                    if src_prop_name:
                        on_expr = f"$this->{src_prop_name.lstrip('$')}"
                        on_kind = "property"
                    else:
                        on_expr = "$this"
                        on_kind = "self"
                elif recv_kind == "parameter":
                    on_expr = recv_name
                    on_kind = "param"
                elif recv_kind == "local":
                    on_expr = recv_name
                    on_kind = "local"
                elif recv_kind == "result" and src_prop_name:
                    # Chain access: e.g., $this->orderService or $customer->address
                    src_recv_kind = recv_rec.get("src_recv_value_kind")
                    src_recv_name = recv_rec.get("src_recv_name")
                    prop_display = src_prop_name.lstrip("$")
                    if src_recv_kind == "self" or src_recv_kind is None:
                        on_expr = f"$this->{prop_display}"
                    elif src_recv_name:
                        on_expr = f"{src_recv_name}->{prop_display}"
                    else:
                        on_expr = src_prop_fqn
                    on_kind = "property"

            # Build arguments
            arguments = _build_call_arguments(runner, call_id)

        entry = ContextEntry(
            depth=depth,
            node_id=caller_id,
            fqn=caller_fqn,
            kind=r.get("caller_kind"),
            file=r.get("caller_file"),
            line=call_line,
            ref_type=entry_ref_type,
            callee=callee_display if entry_ref_type != "caller" else None,
            on=on_expr,
            on_kind=on_kind,
            arguments=arguments if arguments else None,
            crossed_from=crossed_from_fqn,
        )

        # Recurse
        if depth < max_depth:
            entry.children = build_class_used_by_depth_callers(
                runner, caller_id, depth + 1, max_depth, visited
            )

        entries.append(entry)

    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


# =============================================================================
# build_class_used_by
# =============================================================================


def build_class_used_by(
    runner: QueryRunner,
    node: NodeData,
    max_depth: int = 1,
    limit: int = 100,
) -> list[ContextEntry]:
    """Build the USED BY section for a Class node context.

    Fetches all required data in a single batch call, then applies the handler
    pipeline to produce a priority-ordered list of ContextEntry objects.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node: NodeData for the target class.
        max_depth: Maximum depth for caller chain expansion.
        limit: Maximum number of entries to return.

    Returns:
        Ordered list of ContextEntry objects representing who uses the class.
    """
    data = fetch_class_used_by_data(runner, node.node_id)

    extends_children: list[dict] = data["extends_children"]
    incoming_usages: list[dict] = data["incoming_usages"]
    injected_classes: set[str] = data["injected_classes"]
    call_nodes: list[dict] = data["call_nodes"]
    property_types: list[dict] = data["property_types"]
    ref_type_data: dict[str, dict] = data["ref_type_data"]
    constructor_calls: list[dict] = data["constructor_calls"]
    external_method_calls: list[dict] = data["external_method_calls"]
    external_property_accesses: list[dict] = data["external_property_accesses"]

    # ------------------------------------------------------------------
    # Build extends entries directly from Q1 (bypass handler pipeline)
    # ------------------------------------------------------------------
    extends_entries: list[ContextEntry] = []
    for child in extends_children:
        rel = child.get("rel_type", "EXTENDS").lower()
        ref = "extends" if rel == "extends" else "implements"
        extends_entries.append(ContextEntry(
            depth=1,
            node_id=child["id"],
            fqn=child["fqn"],
            kind=child.get("kind"),
            file=child.get("file"),
            line=child.get("start_line"),
            ref_type=ref,
        ))

    # ------------------------------------------------------------------
    # Build call node indices:
    # 1. by (source_id, file, line) for exact match
    # 2. by source_id for checking any constructor call within source
    # ------------------------------------------------------------------
    call_index: dict[tuple, dict] = {}
    calls_by_source: dict[str, list[dict]] = {}
    for cn in call_nodes:
        key = (cn["source_id"], cn.get("call_file"), cn.get("call_start_line"))
        # Keep first match (there may be multiple calls; we want the best match)
        if key not in call_index:
            call_index[key] = cn
        calls_by_source.setdefault(cn["source_id"], []).append(cn)

    # ------------------------------------------------------------------
    # Build property_type lookup: source_id -> prop record (from Q6)
    # For constructor promotion: method -> prop via type_hint
    # ------------------------------------------------------------------
    # Q6 gives us properties by TYPE_HINT. We need to map them back to
    # the method source. We index by source_id where source_kind is Method
    # by checking the Q2 source mapping.
    prop_type_by_source: dict[str, dict] = {}
    for pt in property_types:
        # We'll match these up during the Q2 loop below
        prop_type_by_source[pt["prop_id"]] = pt

    # ------------------------------------------------------------------
    # Process incoming usage edges through handlers
    # ------------------------------------------------------------------
    bucket = EntryBucket()
    classes_with_injection: frozenset[str] = frozenset(injected_classes)

    # Build set of IDs already handled by extends/implements (Q1)
    # to avoid creating duplicate type_hint entries for the same source
    extends_child_ids: set[str] = {child["id"] for child in extends_children}

    for usage in incoming_usages:
        source_id: str = usage["source_id"]

        # Skip sources already handled by extends/implements (Q1)
        if source_id in extends_child_ids:
            continue

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
        containing_class_fqn = usage.get("containing_class_fqn")
        containing_class_kind = usage.get("containing_class_kind")
        containing_class_file = usage.get("containing_class_file")
        containing_class_start_line = usage.get("containing_class_start_line")
        containing_method_id = usage.get("containing_method_id")
        containing_method_fqn = usage.get("containing_method_fqn")
        containing_method_kind = usage.get("containing_method_kind")

        # ------------------------------------------------------------------
        # Look up pre-fetched ref type data from Q8
        # ------------------------------------------------------------------
        rtd = ref_type_data.get(source_id, {})
        has_arg_type_hint: bool = bool(rtd.get("has_arg_type_hint", False))
        has_return_type_hint: bool = bool(rtd.get("has_return_type_hint", False))
        has_class_property_type_hint: bool = bool(rtd.get("has_class_property_type_hint", False))
        has_source_class_property_type_hint: bool = bool(
            rtd.get("has_source_class_property_type_hint", False)
        )

        # ------------------------------------------------------------------
        # Infer reference type
        # ------------------------------------------------------------------
        # Normalise edge_type to lowercase for infer_reference_type
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

        # ------------------------------------------------------------------
        # Find matching Call node from Q4 for this edge location
        # ------------------------------------------------------------------
        call_key = (source_id, edge_file, edge_line)
        call_record = call_index.get(call_key)

        call_node_id = call_record["call_id"] if call_record else None
        call_kind = call_record["call_kind"] if call_record else None

        # Use call_kind for authoritative ref_type override
        if call_kind == "constructor":
            ref_type = "instantiation"
        elif call_kind is None:
            # No exact (source, file, line) match — check if source has ANY
            # constructor call to this target (common when the USES edge
            # location points to the parameter type declaration, not the
            # `new` expression).
            source_calls = calls_by_source.get(source_id, [])
            # Only pick constructor calls whose callee matches the target class FQN
            best_ctor = None
            for sc in source_calls:
                if sc.get("call_kind") == "constructor":
                    callee_fqn = sc.get("callee_fqn") or ""
                    if node.fqn and callee_fqn.startswith(node.fqn):
                        best_ctor = sc
                        break
            if best_ctor is not None:
                call_record = best_ctor
                call_node_id = best_ctor["call_id"]
                call_kind = "constructor"
                ref_type = "instantiation"

        # ------------------------------------------------------------------
        # Resolve receiver / access_chain info from call record
        # ------------------------------------------------------------------
        access_chain: str | None = None
        on_kind: str | None = None
        if call_record and call_node_id:
            access_chain, _ac_symbol = _resolve_call_access_chain(runner, call_node_id)
            # If access chain resolved and there's a symbol, format on with FQN
            if access_chain and _ac_symbol:
                on_kind = "property"
            else:
                recv_value_kind = call_record.get("recv_value_kind")
                if recv_value_kind == "parameter":
                    on_kind = "param"
                elif recv_value_kind == "local":
                    on_kind = "local"
                elif recv_value_kind == "self":
                    on_kind = "self"
                elif recv_value_kind == "result":
                    on_kind = "property"
                elif recv_value_kind:
                    on_kind = recv_value_kind

        # ------------------------------------------------------------------
        # Resolve arguments from Call node
        # ------------------------------------------------------------------
        call_arguments: tuple = ()
        if call_node_id:
            arg_infos = _build_call_arguments(runner, call_node_id)
            call_arguments = tuple(arg_infos)

        # ------------------------------------------------------------------
        # Resolve property data for PropertyTypeHandler (constructor promotion)
        # ------------------------------------------------------------------
        property_node_id: str | None = None
        property_fqn: str | None = None
        property_file: str | None = None
        property_start_line: int | None = None

        if ref_type == "property_type" and source_kind in ("Method", "Function"):
            # Constructor promotion: find the property with type_hint via Q6 data.
            # The property FQN typically shares the class prefix with the method FQN.
            if "::" in source_fqn:
                class_prefix = source_fqn.rsplit("::", 1)[0]
                for pt in property_types:
                    if pt["prop_fqn"].startswith(class_prefix + "::"):
                        property_node_id = pt["prop_id"]
                        property_fqn = pt["prop_fqn"]
                        property_file = pt.get("prop_file")
                        property_start_line = pt.get("prop_start_line")
                        break

        # ------------------------------------------------------------------
        # Build EdgeContext and dispatch to handler
        # ------------------------------------------------------------------
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
            containing_class_fqn=containing_class_fqn,
            containing_class_kind=containing_class_kind,
            containing_class_file=containing_class_file,
            containing_class_start_line=containing_class_start_line,
            call_kind=call_kind,
            access_chain=access_chain,
            on_kind=on_kind,
            arguments=call_arguments,
            property_node_id=property_node_id,
            property_fqn=property_fqn,
            property_file=property_file,
            property_start_line=property_start_line,
        )

        handler = USED_BY_HANDLERS.get(ref_type)
        if handler:
            handler.handle(ctx, bucket)

    # ------------------------------------------------------------------
    # Convert handler buckets to ContextEntry lists
    # ------------------------------------------------------------------
    result_extends = extends_entries  # already ContextEntry
    result_instantiation = [_dict_to_context_entry(d) for d in bucket.instantiation]
    result_property_type = [_dict_to_context_entry(d) for d in bucket.property_type]
    result_method_call = [_dict_to_context_entry(d) for d in bucket.method_call]
    result_property_access = _build_property_access_entries(bucket, node.fqn, depth=1)
    result_param_return = [_dict_to_context_entry(d) for d in bucket.param_return]

    # ------------------------------------------------------------------
    # Process constructor instantiation calls (Q9)
    # ------------------------------------------------------------------
    seen_instantiation_methods: set[str] = set(bucket.seen_instantiation_methods)
    for cc in constructor_calls:
        method_id = cc["method_id"]
        if method_id in seen_instantiation_methods:
            continue
        seen_instantiation_methods.add(method_id)
        call_id = cc.get("call_id")
        args = _build_call_arguments(runner, call_id) if call_id else []
        # Prefer edge location line (matches kloc-cli's USES edge location)
        entry_line = cc.get("edge_line") if cc.get("edge_line") is not None else cc.get("call_line")
        entry_file = cc.get("edge_file") or cc.get("call_file") or cc.get("method_file")
        entry = ContextEntry(
            depth=1,
            node_id=method_id,
            fqn=format_method_fqn(cc.get("method_fqn", ""), cc.get("method_kind", "")),
            kind=cc.get("method_kind"),
            file=entry_file,
            line=entry_line,
            ref_type="instantiation",
            arguments=list(args),
        )
        result_instantiation.append(entry)

    # ------------------------------------------------------------------
    # Process external method calls (Q10)
    # ------------------------------------------------------------------
    seen_method_calls: set[str] = {e.node_id for e in result_method_call}
    for emc in external_method_calls:
        caller_id = emc["caller_id"]
        if caller_id in seen_method_calls:
            continue
        # Suppress if caller's class has injection (property_type)
        caller_class_id = emc.get("caller_class_id")
        if caller_class_id and caller_class_id in injected_classes:
            continue

        seen_method_calls.add(caller_id)

        callee_name = emc.get("callee_name")
        callee_kind = emc.get("callee_kind", "")
        callee_display = (callee_name + "()") if callee_kind == "Method" and callee_name else callee_name

        # Build access chain from receiver
        recv_value_kind = emc.get("recv_value_kind")
        recv_name = emc.get("recv_name")
        recv_prop_fqn = emc.get("recv_prop_fqn")
        recv_prop_name = emc.get("recv_prop_name")
        src_recv_value_kind = emc.get("src_recv_value_kind")
        src_recv_name = emc.get("src_recv_name")

        on_expr, on_kind = _build_on_from_receiver(
            recv_value_kind, recv_name, recv_prop_fqn, recv_prop_name,
            src_recv_value_kind, src_recv_name,
        )

        mc_line = emc.get("edge_line") if emc.get("edge_line") is not None else emc.get("call_line")
        mc_file = emc.get("edge_file") or emc.get("call_file") or emc.get("caller_file")
        entry = ContextEntry(
            depth=1,
            node_id=caller_id,
            fqn=format_method_fqn(emc.get("caller_fqn", ""), emc.get("caller_kind", "")),
            kind=emc.get("caller_kind"),
            file=mc_file,
            line=mc_line,
            ref_type="method_call",
            callee=callee_display,
            on=on_expr,
            on_kind=on_kind,
        )
        result_method_call.append(entry)

    # ------------------------------------------------------------------
    # Process external property accesses (Q11) into property access groups
    # Uses USES edges (not Call nodes) for accurate access counting.
    # ------------------------------------------------------------------
    for epa in external_property_accesses:
        prop_fqn = epa.get("prop_fqn", "")
        caller_id = epa["caller_id"]
        caller_fqn = epa.get("caller_fqn", "")
        caller_kind = epa.get("caller_kind", "")
        call_line = epa.get("call_line")

        # Resolve on_expr and on_kind from receiver info
        recv_name = epa.get("recv_name")
        recv_value_kind = epa.get("recv_value_kind")
        src_prop_name = epa.get("src_prop_name")
        src_prop_fqn = epa.get("src_prop_fqn")
        src_recv_value_kind = epa.get("src_recv_value_kind")
        src_recv_name = epa.get("src_recv_name")

        on_expr, on_kind = _build_on_from_receiver(
            recv_value_kind, recv_name, src_prop_fqn, src_prop_name,
            src_recv_value_kind, src_recv_name,
        )

        if prop_fqn not in bucket.property_access_groups:
            bucket.property_access_groups[prop_fqn] = []

        # Group by (method, on_expr, on_kind)
        found = False
        for group_entry in bucket.property_access_groups[prop_fqn]:
            if (group_entry["method_fqn"] == caller_fqn
                    and group_entry["on_expr"] == on_expr
                    and group_entry["on_kind"] == on_kind):
                group_entry["lines"].append(call_line)
                found = True
                break
        if not found:
            bucket.property_access_groups[prop_fqn].append({
                "method_fqn": caller_fqn,
                "method_id": caller_id,
                "method_kind": caller_kind,
                "lines": [call_line],
                "on_expr": on_expr,
                "on_kind": on_kind,
                "file": epa.get("call_file") or epa.get("caller_file"),
            })

    # Rebuild property access entries with the new groups
    result_property_access = _build_property_access_entries(bucket, node.fqn, depth=1)

    # ------------------------------------------------------------------
    # Depth 2+: expand depth callers for chainable entries
    # ------------------------------------------------------------------
    if max_depth >= 2:
        # Build visited_sources like kloc-cli: includes the target class and
        # all depth-1 source node IDs so that depth-2 expansion doesn't revisit them.
        visited_sources: set[str] = {node.node_id}
        for entry in result_extends:
            if entry.node_id:
                visited_sources.add(entry.node_id)
        for entry in result_instantiation:
            if entry.node_id:
                visited_sources.add(entry.node_id)
        for entry in result_property_type:
            if entry.node_id:
                visited_sources.add(entry.node_id)
        for entry in result_method_call:
            if entry.node_id:
                visited_sources.add(entry.node_id)
        for entry in result_property_access:
            if entry.node_id:
                visited_sources.add(entry.node_id)
        for entry in result_param_return:
            if entry.node_id:
                visited_sources.add(entry.node_id)
        # Also add method IDs from external property accesses (Q11) and
        # external method calls (Q10) — these are depth-1 callers that should
        # be in visited_sources to prevent re-visiting at depth 2.
        for epa in external_property_accesses:
            caller_id = epa.get("caller_id")
            if caller_id:
                visited_sources.add(caller_id)
        for emc in external_method_calls:
            caller_id = emc.get("caller_id")
            if caller_id:
                visited_sources.add(caller_id)

        # Expand extends entries with override methods
        for entry in result_extends:
            if entry.node_id and entry.ref_type in ("extends", "implements"):
                override_records = runner.execute(
                    _Q_OVERRIDE_METHODS, subclass_id=entry.node_id
                )
                override_children: list[ContextEntry] = []
                for ov in override_records:
                    ov_entry = ContextEntry(
                        depth=2,
                        node_id=ov["method_id"],
                        fqn=ov.get("method_fqn", ""),
                        kind="Method",
                        file=ov.get("method_file"),
                        line=ov.get("method_start_line"),
                        signature=ov.get("method_signature"),
                        ref_type="override",
                    )
                    override_children.append(ov_entry)
                entry.children = override_children

        # Expand method_call entries — each gets a fresh copy of visited_sources
        for entry in result_method_call:
            if entry.node_id and entry.ref_type in CHAINABLE_REFERENCE_TYPES:
                entry.children = build_class_used_by_depth_callers(
                    runner, entry.node_id, 2, max_depth, set(visited_sources)
                )

        # Expand instantiation entries — each gets a fresh copy of visited_sources
        for entry in result_instantiation:
            if entry.node_id and entry.ref_type in CHAINABLE_REFERENCE_TYPES:
                entry.children = build_class_used_by_depth_callers(
                    runner, entry.node_id, 2, max_depth, set(visited_sources)
                )

        # For property_type entries at depth 2, add injection point calls
        for entry in result_property_type:
            if entry.node_id and entry.fqn:
                # Resolve the containing class FQN for crossed_from
                _cls_rec = runner.execute_single(
                    "MATCH (prop:Node {node_id: $prop_id})<-[:CONTAINS*]-(cls) "
                    "WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum'] "
                    "RETURN cls.fqn AS cls_fqn LIMIT 1",
                    prop_id=entry.node_id,
                )
                crossed_from_cls = _cls_rec["cls_fqn"] if _cls_rec else None

                injection_records = runner.execute(
                    Q7_INJECTION_POINT_CALLS,
                    property_id=entry.node_id,
                    property_fqn=entry.fqn,
                )
                # Dedup by callee FQN (collect sites if same callee from multiple methods)
                entries_by_callee: dict[str, ContextEntry] = {}
                for r in injection_records:
                    callee_id = r.get("callee_id")
                    callee_fqn_raw = r.get("callee_fqn", "")
                    callee_name = r.get("callee_name")
                    callee_kind = r.get("callee_kind", "")
                    call_id = r.get("call_id")
                    call_line = r.get("call_line")
                    method_fqn_raw = r.get("method_fqn", "")

                    callee_display = (
                        callee_name + "()"
                        if callee_kind == "Method" and callee_name and not callee_name.endswith("()")
                        else callee_name
                    )

                    # Build on from receiver resolution
                    on_expr = None
                    if call_id:
                        ac_rec = runner.execute_single(
                            _Q_RESOLVE_ACCESS_CHAIN, call_id=call_id
                        )
                        if ac_rec:
                            on_expr, _ = _resolve_call_access_chain(runner, call_id)

                    # Build arguments
                    arguments = _build_call_arguments(runner, call_id) if call_id else []

                    # Get call file
                    call_file_rec = runner.execute_single(
                        "MATCH (c:Node {node_id: $cid}) RETURN c.file AS f",
                        cid=call_id,
                    ) if call_id else None
                    call_file = call_file_rec["f"] if call_file_rec else None

                    # Dedup by callee FQN
                    callee_key = callee_fqn_raw
                    if callee_key in entries_by_callee:
                        existing_entry = entries_by_callee[callee_key]
                        # Extract method short name for sites
                        method_short = method_fqn_raw.rsplit("::", 1)[-1] if "::" in method_fqn_raw else method_fqn_raw
                        if existing_entry.sites is None:
                            # First site was the existing entry's line
                            prev_method_line = existing_entry.line
                            existing_entry.sites = [{"method": method_short, "line": prev_method_line}]
                            existing_entry.line = None
                        existing_entry.sites.append({"method": method_short, "line": call_line})
                        continue

                    child = ContextEntry(
                        depth=2,
                        node_id=callee_id or "",
                        fqn=format_method_fqn(callee_fqn_raw, callee_kind),
                        kind=callee_kind,
                        file=call_file,
                        line=call_line,
                        ref_type="method_call",
                        callee=callee_display,
                        on=on_expr,
                        on_kind="property" if on_expr else None,
                        arguments=arguments if arguments else None,
                        crossed_from=crossed_from_cls,
                    )
                    entries_by_callee[callee_key] = child
                    entry.children.append(child)

                entry.children.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))

        # Expand property_access entries with per-method children
        for entry in result_property_access:
            prop_fqn = entry.node_id  # We stored prop_fqn as node_id
            groups = bucket.property_access_groups.get(prop_fqn, [])
            method_children: list[ContextEntry] = []
            for group in groups:
                method_fqn = group["method_fqn"]
                method_id_val = group["method_id"]
                method_kind = group.get("method_kind", "Method")

                # Build short display FQN: "ClassName::method()"
                method_short = method_fqn.split("::")[-1] if "::" in method_fqn else method_fqn
                if method_kind == "Method" and not method_short.endswith("()"):
                    method_short = method_short + "()"
                class_part = method_fqn.split("::")[0].split("\\")[-1] if "::" in method_fqn else ""
                child_display = f"{class_part}::{method_short}" if class_part else method_short

                lines = [ln for ln in group["lines"] if ln is not None]
                lines_sorted = sorted(lines)
                first_line = lines_sorted[0] if lines_sorted else None

                sites = None
                if len(lines_sorted) > 1:
                    sites = [{"line": ln} for ln in lines_sorted]

                child_entry = ContextEntry(
                    depth=2,
                    node_id=method_id_val,
                    fqn=child_display,
                    kind=method_kind,
                    file=group.get("file"),
                    line=first_line,
                    ref_type="property_access",
                    on=group.get("on_expr"),
                    on_kind=group.get("on_kind"),
                    sites=sites,
                )

                # Depth 3+: add caller chain for each method
                if max_depth >= 3 and method_id_val:
                    child_entry.children = build_class_used_by_depth_callers(
                        runner, method_id_val, 3, max_depth, set(visited_sources)
                    )

                method_children.append(child_entry)

            method_children.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
            entry.children = method_children

    # ------------------------------------------------------------------
    # Sort each bucket by (file, line) to match kloc-cli ordering
    # ------------------------------------------------------------------
    def _sort_key(e: ContextEntry) -> tuple:
        return (e.file or "", e.line if e.line is not None else 0)

    result_extends.sort(key=_sort_key)
    result_instantiation.sort(key=_sort_key)
    result_property_type.sort(key=_sort_key)
    result_method_call.sort(key=_sort_key)
    result_property_access.sort(key=lambda e: e.fqn or "")
    result_param_return.sort(key=_sort_key)

    # ------------------------------------------------------------------
    # Combine in priority order and apply limit
    # ------------------------------------------------------------------
    all_entries = (
        result_extends
        + result_instantiation
        + result_property_type
        + result_method_call
        + result_property_access
        + result_param_return
    )

    return all_entries[:limit]


# =============================================================================
# USES depth-2 helpers for extends/implements
# =============================================================================

# Query: find override methods in a class that override methods of a parent/interface
_Q_USES_OVERRIDE_METHODS = """
MATCH (cls:Node {node_id: $class_id})-[:CONTAINS]->(method:Method)
WHERE method.name <> '__construct'
MATCH (method)-[:OVERRIDES]->(parent_method:Method)
RETURN DISTINCT method.node_id AS method_id,
       method.fqn AS method_fqn,
       method.file AS method_file,
       method.start_line AS method_start_line,
       method.signature AS method_signature
ORDER BY method.file, method.start_line
"""

# Query: find extends children of a class recursively (for implements depth-2)
_Q_EXTENDS_CHILDREN_FOR_USES = """
MATCH (cls:Node {node_id: $class_id})<-[:EXTENDS*]-(child)
WHERE child.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN child.node_id AS child_id,
       child.fqn AS child_fqn,
       child.kind AS child_kind,
       child.file AS child_file,
       child.start_line AS child_start_line
ORDER BY child.file, child.start_line
"""


def _build_implements_depth2_uses(
    runner: QueryRunner, class_id: str, interface_id: str,
) -> list[ContextEntry]:
    """Build depth-2 children for [implements] in USES.

    Shows override methods and extends subclasses.
    """
    override_entries: list[ContextEntry] = []
    extends_entries: list[ContextEntry] = []

    # Find override methods
    override_records = runner.execute(_Q_USES_OVERRIDE_METHODS, class_id=class_id)
    for ov in override_records:
        ov_entry = ContextEntry(
            depth=2,
            node_id=ov["method_id"],
            fqn=ov.get("method_fqn", ""),
            kind="Method",
            file=ov.get("method_file"),
            line=ov.get("method_start_line"),
            signature=ov.get("method_signature"),
            ref_type="override",
        )
        override_entries.append(ov_entry)

    # Find extends subclasses
    extends_records = runner.execute(_Q_EXTENDS_CHILDREN_FOR_USES, class_id=class_id)
    for ext in extends_records:
        ext_entry = ContextEntry(
            depth=2,
            node_id=ext["child_id"],
            fqn=ext.get("child_fqn", ""),
            kind=ext.get("child_kind"),
            file=ext.get("child_file"),
            line=ext.get("child_start_line"),
            ref_type="extends",
        )
        extends_entries.append(ext_entry)

    return override_entries + extends_entries


def _build_extends_depth2_uses(
    runner: QueryRunner, class_id: str, parent_id: str,
) -> list[ContextEntry]:
    """Build depth-2 children for [extends] in USES.

    Shows override methods and inherited methods from the parent.
    """
    override_entries: list[ContextEntry] = []

    # Find override methods in the extending class
    override_records = runner.execute(_Q_USES_OVERRIDE_METHODS, class_id=class_id)
    for ov in override_records:
        ov_entry = ContextEntry(
            depth=2,
            node_id=ov["method_id"],
            fqn=ov.get("method_fqn", ""),
            kind="Method",
            file=ov.get("method_file"),
            line=ov.get("method_start_line"),
            signature=ov.get("method_signature"),
            ref_type="override",
        )
        override_entries.append(ov_entry)

    return override_entries


# =============================================================================
# build_class_uses
# =============================================================================


def build_class_uses(
    runner: QueryRunner,
    node: NodeData,
    max_depth: int = 1,
    limit: int = 100,
) -> list[ContextEntry]:
    """Build the USES section for a Class node context.

    Fetches outgoing dependencies (structural and member-level) and assembles
    a priority-ordered list of ContextEntry objects.

    Key logic (matching kloc-cli):
    1. Pre-collect type_hint edges from class members (Property -> property_type,
       Method -> return_type, Argument -> parameter_type).
    2. Pre-collect constructor calls for instantiation detection.
    3. Resolve member-level targets (Method, Property) to containing class.
    4. Deduplicate by resolved class with priority ordering.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node: NodeData for the target class.
        max_depth: Maximum depth for behavioral expansion (depth 2 adds
            methods called through injected properties).
        limit: Maximum number of entries to return.

    Returns:
        Priority-ordered list of ContextEntry objects for USES section.
    """
    from ..db.queries.context_class_uses import (
        Q6_TARGET_CONTAINING_CLASS,
    )

    data = fetch_class_uses_data(runner, node.node_id)

    member_deps: list[dict] = data["member_deps"]
    class_rel: list[dict] = data["class_rel"]
    type_hints: list[dict] = data["type_hints"]
    ctor_calls: list[dict] = data["ctor_calls"]

    # ------------------------------------------------------------------
    # Class-level structural relationships (extends, implements, uses_trait)
    # ------------------------------------------------------------------
    # target_info maps resolved_target_id -> best info
    target_info: dict[str, dict] = {}

    for rel in class_rel:
        rel_type_raw: str = rel.get("rel_type", "EXTENDS").upper()
        if rel_type_raw == "EXTENDS":
            ref = "extends"
        elif rel_type_raw == "IMPLEMENTS":
            ref = "implements"
        else:
            ref = "uses_trait"

        target_id = rel.get("target_id", "")
        target_info[target_id] = {
            "target_id": target_id,
            "target_fqn": rel.get("target_fqn", ""),
            "target_kind": rel.get("target_kind"),
            "ref_type": ref,
            # Use source class file/line for extends/implements (matches kloc-cli)
            "file": node.file,
            "line": node.start_line,
            "property_name": None,
        }

    # ------------------------------------------------------------------
    # Pre-collect type_hint classification from Q4
    # Maps target_id -> {ref_type, property_name, file, line}
    # Priority: property_type > parameter_type > return_type
    # ------------------------------------------------------------------
    type_hint_info: dict[str, dict] = {}

    for th in type_hints:
        member_kind = th.get("member_kind")
        member_name = th.get("member_name")
        member_file = th.get("member_file")
        member_line = th.get("member_line")

        # Property TYPE_HINT -> property_type
        th_target_id = th.get("th_target_id")
        if member_kind == "Property" and th_target_id:
            prop_name = member_name or ""
            if prop_name and not prop_name.startswith("$"):
                prop_name = "$" + prop_name
            type_hint_info[th_target_id] = {
                "ref_type": "property_type",
                "property_name": prop_name,
                "file": member_file,
                "line": member_line,
            }

        # Method TYPE_HINT -> return_type (unless already property_type)
        if member_kind == "Method" and th_target_id:
            existing_th = type_hint_info.get(th_target_id)
            if not existing_th:
                type_hint_info[th_target_id] = {
                    "ref_type": "return_type",
                    "property_name": None,
                    "file": member_file,
                    "line": member_line,
                }
            elif existing_th["ref_type"] == "return_type":
                # Pick the earlier line for consistent ordering
                if member_line is not None and (existing_th["line"] is None or member_line < existing_th["line"]):
                    existing_th["file"] = member_file
                    existing_th["line"] = member_line

        # Argument TYPE_HINT -> parameter_type (wins over return_type, not property_type)
        arg_target_id = th.get("arg_target_id")
        if member_kind == "Method" and arg_target_id:
            existing = type_hint_info.get(arg_target_id)
            if not existing or existing["ref_type"] == "return_type":
                type_hint_info[arg_target_id] = {
                    "ref_type": "parameter_type",
                    "property_name": None,
                    "file": member_file,
                    "line": member_line,
                }
            elif existing["ref_type"] == "parameter_type":
                # Pick the earliest line for consistent ordering
                if member_line is not None and (existing["line"] is None or member_line < existing["line"]):
                    existing["file"] = member_file
                    existing["line"] = member_line

    # ------------------------------------------------------------------
    # Pre-collect instantiation targets from Q5
    # ------------------------------------------------------------------
    instantiation_targets: dict[str, dict] = {}
    for cc in ctor_calls:
        tid = cc.get("target_id", "")
        if tid not in instantiation_targets:
            instantiation_targets[tid] = {
                "file": cc.get("call_file"),
                "line": cc.get("call_line"),
            }

    # ------------------------------------------------------------------
    # USES dedup priority (kloc-cli style)
    # ------------------------------------------------------------------
    uses_dedup_priority = {
        "instantiation": 0,
        "property_type": 1,
        "method_call": 2,
        "property_access": 2,
        "parameter_type": 3,
        "return_type": 4,
        "type_hint": 5,
    }

    # ------------------------------------------------------------------
    # Process member deps: resolve to containing class, classify, dedup
    # ------------------------------------------------------------------
    for dep in member_deps:
        dep_target_id: str = dep.get("target_id", "")
        dep_target_kind: str = dep.get("target_kind", "")
        dep_target_fqn: str = dep.get("target_fqn", "")
        dep_file = dep.get("file")
        dep_line = dep.get("line")

        # Resolve member-level targets to their containing class
        resolved_id = dep_target_id
        resolved_fqn = dep_target_fqn
        resolved_kind = dep_target_kind

        if dep_target_kind in ("Method", "Property", "Argument", "Value", "Call", "Constant", "Function"):
            rec = runner.execute_single(
                Q6_TARGET_CONTAINING_CLASS, node_id=dep_target_id
            )
            if rec:
                resolved_id = rec["class_id"]
                resolved_fqn = rec["class_fqn"]
                resolved_kind = rec["class_kind"]
            else:
                continue

        # Skip self-references
        if resolved_id == node.node_id:
            continue

        # Skip already-tracked extends/implements
        existing = target_info.get(resolved_id)
        if existing and existing["ref_type"] in ("extends", "implements", "uses_trait"):
            continue

        # Classify using pre-collected info
        ref_type = None
        property_name = None

        # Check type_hint-based classification (property_type or return_type first)
        if resolved_id in type_hint_info:
            th_info = type_hint_info[resolved_id]
            th_ref = th_info["ref_type"]
            if th_ref in ("property_type", "return_type"):
                ref_type = th_ref
                property_name = th_info.get("property_name")
                dep_file = th_info["file"] or dep_file
                dep_line = th_info["line"] if th_info["line"] is not None else dep_line

        # Check for instantiation
        if ref_type is None and resolved_id in instantiation_targets:
            inst_info = instantiation_targets[resolved_id]
            ref_type = "instantiation"
            dep_file = inst_info["file"] or dep_file
            dep_line = inst_info["line"] if inst_info["line"] is not None else dep_line

        # Fall back to remaining type_hint classification (parameter_type)
        if ref_type is None and resolved_id in type_hint_info:
            th_info = type_hint_info[resolved_id]
            ref_type = th_info["ref_type"]
            property_name = th_info.get("property_name")
            dep_file = th_info["file"] or dep_file
            dep_line = th_info["line"] if th_info["line"] is not None else dep_line

        # Fall back to edge-level inference
        if ref_type is None:
            ref_type = infer_reference_type(
                edge_type=dep.get("edge_type", "USES").lower(),
                target_kind=dep_target_kind,
                source_kind=dep.get("member_kind"),
                source_id=dep.get("member_id", ""),
                target_id=dep_target_id,
                source_name=dep.get("member_name"),
            )

        # Dedup by resolved target
        if resolved_id in target_info:
            existing = target_info[resolved_id]
            existing_priority = uses_dedup_priority.get(existing["ref_type"], 10)
            new_priority = uses_dedup_priority.get(ref_type, 10)
            if new_priority < existing_priority:
                target_info[resolved_id] = {
                    "target_id": resolved_id,
                    "target_fqn": resolved_fqn,
                    "target_kind": resolved_kind,
                    "ref_type": ref_type,
                    "file": dep_file or existing.get("file"),
                    "line": dep_line if dep_line is not None else existing.get("line"),
                    "property_name": property_name or existing.get("property_name"),
                }
        else:
            target_info[resolved_id] = {
                "target_id": resolved_id,
                "target_fqn": resolved_fqn,
                "target_kind": resolved_kind,
                "ref_type": ref_type,
                "file": dep_file,
                "line": dep_line,
                "property_name": property_name,
            }

    # ------------------------------------------------------------------
    # Convert target_info to ContextEntry list
    # ------------------------------------------------------------------
    all_entries: list[ContextEntry] = []
    for td in target_info.values():
        entry = ContextEntry(
            depth=1,
            node_id=td["target_id"],
            fqn=td["target_fqn"],
            kind=td.get("target_kind"),
            file=td.get("file"),
            line=td.get("line"),
            ref_type=td["ref_type"],
            property_name=td.get("property_name"),
        )
        all_entries.append(entry)

    # ------------------------------------------------------------------
    # Sort by USES priority order
    # ------------------------------------------------------------------
    # Secondary tiebreaker: parameter_type sorts before return_type
    _ref_type_secondary = {
        "parameter_type": 0,
        "return_type": 1,
    }
    all_entries.sort(
        key=lambda e: (
            USES_PRIORITY.get(e.ref_type or "", 99),
            e.file or "",
            e.line or 0,
            _ref_type_secondary.get(e.ref_type or "", 5),
        )
    )

    # ------------------------------------------------------------------
    # Depth 2: behavioral expansion for property_type entries
    # ------------------------------------------------------------------
    if max_depth >= 2:
        for entry in all_entries:
            if entry.ref_type == "property_type" and entry.node_id:
                # Behavioral depth-2: methods called through injected property
                prop_fqn = entry.property_name
                if prop_fqn:
                    # Build the full property FQN: ClassName::$prop
                    class_prefix = node.fqn
                    full_prop_fqn = f"{class_prefix}::{prop_fqn}"
                else:
                    full_prop_fqn = entry.fqn

                records = runner.execute(
                    Q3_BEHAVIORAL_DEPTH2,
                    class_id=node.node_id,
                    property_fqn=full_prop_fqn,
                )
                seen: set[str] = set()
                for r in records:
                    callee_id = r["callee_id"]
                    if callee_id in seen:
                        continue
                    seen.add(callee_id)
                    callee_kind = r.get("callee_kind", "")
                    callee_name = r.get("callee_name", "")
                    callee_display = (
                        callee_name + "()"
                        if callee_kind == "Method" and not callee_name.endswith("()")
                        else callee_name
                    )
                    # Build arguments from the call node
                    call_id = r.get("call_id")
                    arguments = _build_call_arguments(runner, call_id) if call_id else []

                    # Resolve on_expr from access chain
                    on_expr = None
                    if call_id:
                        on_expr, _ = _resolve_call_access_chain(runner, call_id)

                    child = ContextEntry(
                        depth=2,
                        node_id=callee_id,
                        fqn=format_method_fqn(r.get("callee_fqn", ""), callee_kind),
                        kind=callee_kind,
                        file=r.get("call_file"),
                        line=r.get("call_line"),
                        ref_type="method_call",
                        callee=callee_display,
                        on=on_expr,
                        on_kind="property" if on_expr else None,
                        arguments=arguments if arguments else None,
                    )
                    entry.children.append(child)

    # ------------------------------------------------------------------
    # Depth-2: extends/implements expansion (overrides + subclasses)
    # ------------------------------------------------------------------
    if max_depth >= 2:
        for entry in all_entries:
            if entry.ref_type == "implements" and entry.node_id:
                entry.children = _build_implements_depth2_uses(
                    runner, node.node_id, entry.node_id
                )
            elif entry.ref_type == "extends" and entry.node_id:
                entry.children = _build_extends_depth2_uses(
                    runner, node.node_id, entry.node_id
                )

    # ------------------------------------------------------------------
    # Depth-2+: recursive class USES expansion for non-structural deps
    # ------------------------------------------------------------------
    if max_depth >= 2:
        parent_visited: set[str] = {node.node_id}
        for entry in all_entries:
            # Skip entries already handled by Q3 (property_type) or
            # requiring specific handlers (extends, implements)
            if entry.ref_type in ("property_type", "extends", "implements", "uses_trait"):
                continue
            if entry.node_id and entry.kind in ("Class", "Interface", "Trait", "Enum"):
                entry.children = build_class_uses_recursive(
                    runner, entry.node_id, 2, max_depth, limit,
                    visited=set(parent_visited),
                )

    return all_entries[:limit]


# =============================================================================
# build_class_uses_recursive
# =============================================================================

# Query to resolve a node to its containing class
_Q_CONTAINING_CLASS_FOR_NODE = """
MATCH (n:Node {node_id: $node_id})
OPTIONAL MATCH path = (n)<-[:CONTAINS*1..5]-(ancestor)
WHERE ancestor.kind IN ['Class', 'Interface', 'Trait', 'Enum']
WITH n, ancestor, path ORDER BY length(path) ASC LIMIT 1
RETURN COALESCE(ancestor.node_id, n.node_id) AS class_id,
       COALESCE(ancestor.fqn, n.fqn) AS class_fqn,
       COALESCE(ancestor.kind, n.kind) AS class_kind,
       COALESCE(ancestor.file, n.file) AS class_file,
       COALESCE(ancestor.start_line, n.start_line) AS class_line
"""


def build_class_uses_recursive(
    runner: QueryRunner,
    target_id: str,
    depth: int,
    max_depth: int,
    limit: int,
    visited: set[str],
) -> list[ContextEntry]:
    """Recursive class-level expansion for USES deps at depth 2+.

    For parameter_type, return_type, property_access, etc. deps, resolves each
    target to its containing class, deduplicates, and returns entries.
    Used by interface USES depth-2 expansion and class USES depth-2+ expansion.

    Args:
        runner: Active QueryRunner.
        target_id: Node ID of the class to expand.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum number of entries.
        visited: Set of visited class IDs for cycle detection.

    Returns:
        List of ContextEntry objects representing the class's USES dependencies.
    """
    if depth > max_depth or target_id in visited:
        return []
    visited.add(target_id)

    data = fetch_class_uses_data(runner, target_id)
    member_deps: list[dict] = data["member_deps"]
    class_rel: list[dict] = data["class_rel"]

    # Fetch class-level USES edges (not from members, but from the class itself).
    # kloc-cli's get_deps(include_members=True) includes these alongside member deps.
    _Q_CLASS_LEVEL_USES = """
    MATCH (cls:Node {node_id: $id})-[e:USES]->(target:Node)
    WHERE target.kind IN ['Class', 'Interface', 'Trait', 'Enum', 'Method', 'Property', 'Function']
      AND NOT EXISTS { MATCH (cls)-[:CONTAINS*]->(target) }
    RETURN cls.node_id AS member_id,
           cls.fqn AS member_fqn,
           cls.kind AS member_kind,
           cls.name AS member_name,
           target.node_id AS target_id,
           target.fqn AS target_fqn,
           target.kind AS target_kind,
           target.name AS target_name,
           'USES' AS edge_type,
           e.loc_file AS file,
           e.loc_line AS line
    """
    cls_uses_records = runner.execute(_Q_CLASS_LEVEL_USES, id=target_id)
    cls_uses_deps = [dict(r) for r in cls_uses_records]

    # Merge class-level USES with member deps. kloc-cli iterates extends first,
    # then all USES (class-level + member) in insertion order. Class-level USES
    # come before member USES in kloc-cli's get_deps().
    all_deps = cls_uses_deps + member_deps

    # Build type_hint lookup for ref_type classification.
    # Q4 returns (member_id, member_kind, th_target_id, arg_target_id) rows.
    type_hints: list[dict] = data["type_hints"]
    # member_id -> set of th_target_ids (return type hints)
    return_th_map: dict[str, set[str]] = {}
    # member_id -> set of arg_target_ids (argument type hints)
    arg_th_map: dict[str, set[str]] = {}
    for th in type_hints:
        mid = th.get("member_id")
        if not mid:
            continue
        th_tid = th.get("th_target_id")
        arg_tid = th.get("arg_target_id")
        if th_tid:
            return_th_map.setdefault(mid, set()).add(th_tid)
        if arg_tid:
            arg_th_map.setdefault(mid, set()).add(arg_tid)

    local_visited: set[str] = set()
    entries: list[ContextEntry] = []

    # Process EXTENDS edges only (kloc-cli only adds extends, NOT implements/uses_trait)
    for rel in class_rel:
        rel_type_raw = rel.get("rel_type", "EXTENDS").upper()
        if rel_type_raw != "EXTENDS":
            continue

        rel_target_id = rel.get("target_id", "")
        if rel_target_id in local_visited or rel_target_id in visited:
            continue
        local_visited.add(rel_target_id)

        entry = ContextEntry(
            depth=depth,
            node_id=rel_target_id,
            fqn=rel.get("target_fqn", ""),
            kind=rel.get("target_kind"),
            file=rel.get("file"),
            line=rel.get("line"),
            ref_type="extends",
        )

        # Recursive expansion
        if depth < max_depth and rel.get("target_kind") in ("Class", "Interface", "Trait", "Enum"):
            entry.children = build_class_uses_recursive(
                runner, rel_target_id, depth + 1, max_depth, limit,
                visited | local_visited,
            )

        entries.append(entry)

    # Process all deps (class-level USES + member USES): resolve to containing class
    for dep in all_deps:
        dep_target_id = dep.get("target_id", "")
        dep_target_kind = dep.get("target_kind", "")
        dep_target_fqn = dep.get("target_fqn", "")
        edge_type = dep.get("edge_type", "USES").lower()

        # Resolve non-class targets to their containing class
        resolved_id = dep_target_id
        resolved_fqn = dep_target_fqn
        resolved_kind = dep_target_kind
        dep_file = dep.get("file")
        dep_line = dep.get("line")

        if dep_target_kind in ("Method", "Property", "Argument", "Value", "Call"):
            rec = runner.execute_single(
                _Q_CONTAINING_CLASS_FOR_NODE, node_id=dep_target_id
            )
            if rec:
                resolved_id = rec["class_id"]
                resolved_fqn = rec["class_fqn"]
                resolved_kind = rec["class_kind"]
            else:
                continue

        if resolved_id == target_id or resolved_id in local_visited or resolved_id in visited:
            continue
        local_visited.add(resolved_id)

        # Infer reference type with type_hint data for accurate classification
        member_id = dep.get("member_id", "")
        has_arg_th = dep_target_id in arg_th_map.get(member_id, set())
        has_return_th = dep_target_id in return_th_map.get(member_id, set())
        ref_type = infer_reference_type(
            edge_type=edge_type,
            target_kind=dep_target_kind,
            source_kind=dep.get("member_kind"),
            source_id=member_id,
            target_id=dep_target_id,
            source_name=dep.get("member_name"),
            has_arg_type_hint=has_arg_th,
            has_return_type_hint=has_return_th,
        )

        entry = ContextEntry(
            depth=depth,
            node_id=resolved_id,
            fqn=resolved_fqn,
            kind=resolved_kind,
            file=dep_file,
            line=dep_line,
            ref_type=ref_type,
        )

        # Recursive expansion
        if depth < max_depth and resolved_kind in ("Class", "Interface", "Trait", "Enum"):
            entry.children = build_class_uses_recursive(
                runner, resolved_id, depth + 1, max_depth, limit,
                visited | local_visited,
            )

        entries.append(entry)

    # Sort by priority
    entries.sort(
        key=lambda e: (
            USES_PRIORITY.get(e.ref_type or "", 99),
            e.file or "",
            e.line or 0,
        )
    )

    return entries[:limit]
