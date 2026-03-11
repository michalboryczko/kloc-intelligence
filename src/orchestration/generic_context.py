"""Generic USED BY handler for method-level context.

Produces ContextEntry objects with member_ref (method-level format)
instead of ref_type (class-level format). Used for Method, Function,
and other non-specialized kinds.

Key rules:
- R1: Filter imports unless with_imports=True
- R3: Filter internal self-references for class queries
- R7: Recursive depth via containing method -> callers
- R8: Only CHAINABLE_REFERENCE_TYPES expand children
- R2: Sort entries by (file, line)
"""

from __future__ import annotations

from ..db.query_runner import QueryRunner
from ..logic.reference_types import CHAINABLE_REFERENCE_TYPES
from ..models.node import NodeData
from ..models.results import ContextEntry, MemberRef, ArgumentInfo

# =============================================================================
# Cypher queries for generic USED BY
# =============================================================================

# Incoming usages grouped by source with edge info and Call node resolution
_Q_INCOMING_USAGES = """
MATCH (target:Node {node_id: $target_id})<-[e:USES]-(source:Node)
WHERE source.kind <> 'File'
OPTIONAL MATCH (source)<-[:CONTAINS*]-(scope)
WHERE scope.kind IN ['Method', 'Function']
WITH source, e, scope
ORDER BY size([(source)<-[:CONTAINS*]-(scope) | 1])
WITH source, e, COLLECT(scope)[0] AS containing_method
RETURN source.node_id AS source_id,
       source.fqn AS source_fqn,
       source.kind AS source_kind,
       source.name AS source_name,
       source.file AS source_file,
       source.start_line AS source_start_line,
       source.signature AS source_signature,
       type(e) AS edge_type,
       e.loc_file AS edge_file,
       e.loc_line AS edge_line,
       containing_method.node_id AS containing_method_id
"""

# Resolve Call node for a usage edge.
# Strategy 1: Find Call nodes contained by source that CALL the target.
_Q_FIND_CALL_BY_TARGET = """
MATCH (source:Node {node_id: $source_id})-[:CONTAINS*]->(call:Call)-[:CALLS]->(target:Node {node_id: $target_id})
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_prop)
RETURN call.node_id AS call_id,
       call.call_kind AS call_kind,
       target.node_id AS callee_id,
       target.fqn AS callee_fqn,
       target.name AS callee_name,
       target.kind AS callee_kind,
       recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       recv_prop.fqn AS recv_prop_fqn,
       recv_prop.kind AS recv_prop_kind
LIMIT 1
"""

# Strategy 2: Find Call nodes targeting the target (no containment constraint).
# Used when source doesn't directly contain the Call (e.g., Call in sub-scope).
_Q_FIND_CALL_TO_TARGET = """
MATCH (call:Call)-[:CALLS]->(target:Node {node_id: $target_id})
WHERE call.file = $file
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_prop)
RETURN call.node_id AS call_id,
       call.call_kind AS call_kind,
       target.node_id AS callee_id,
       target.fqn AS callee_fqn,
       target.name AS callee_name,
       target.kind AS callee_kind,
       recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       recv_prop.fqn AS recv_prop_fqn,
       recv_prop.kind AS recv_prop_kind
ORDER BY abs(call.start_line - $line)
LIMIT 1
"""

# Strategy 3: Constructor fallback — find constructor Call nodes within source
# whose callee's parent matches the target (target is a Class, Call targets __construct).
_Q_FIND_CONSTRUCTOR_CALL = """
MATCH (source:Node {node_id: $source_id})-[:CONTAINS*]->(call:Call {call_kind: 'constructor'})
MATCH (call)-[:CALLS]->(ctor:Node {name: '__construct'})
MATCH (target:Node {node_id: $target_id})-[:CONTAINS]->(ctor)
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
RETURN call.node_id AS call_id,
       'constructor' AS call_kind,
       ctor.node_id AS callee_id,
       ctor.fqn AS callee_fqn,
       ctor.name AS callee_name,
       ctor.kind AS callee_kind,
       recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       NULL AS recv_prop_fqn,
       NULL AS recv_prop_kind
LIMIT 1
"""

# Get argument info for a Call (with type resolution)
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

# Resolve access chain: receiver value + source call + target property
_Q_ACCESS_CHAIN_FULL = """
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

# Resolve containing method for a node (R7)
_Q_CONTAINING_METHOD = """
MATCH (n:Node {node_id: $node_id})
OPTIONAL MATCH (n)<-[:CONTAINS*]-(scope)
WHERE scope.kind IN ['Method', 'Function']
WITH scope
ORDER BY scope IS NOT NULL DESC
RETURN scope.node_id AS method_id
LIMIT 1
"""

# For method nodes, the containing method is the node itself
# Check if a node is a Method/Function
_Q_NODE_KIND = """
MATCH (n:Node {node_id: $node_id})
RETURN n.kind AS kind
"""


def _resolve_reference_type(
    edge_type: str,
    call_kind: str | None,
    callee_kind: str | None,
    source_kind: str | None,
) -> str | None:
    """Infer reference type from edge/call data.

    When a Call node is found, use its call_kind for accurate inference.
    Falls back to edge type for non-Call references.
    """
    # Call node call_kind mapping (matches reference implementation)
    _CALL_KIND_MAP = {
        "method": "method_call",
        "method_static": "static_call",
        "constructor": "instantiation",
        "access": "property_access",
        "access_static": "static_property",
        "function": "function_call",
    }

    # Prefer call_kind mapping when available
    if call_kind and call_kind in _CALL_KIND_MAP:
        return _CALL_KIND_MAP[call_kind]

    # Edge type mapping for structural relationships
    if edge_type == "EXTENDS":
        return "extends"
    if edge_type == "IMPLEMENTS":
        return "implements"
    if edge_type == "USES_TRAIT":
        return "use_trait"

    # Fallback: infer from callee kind
    if callee_kind == "Method":
        return "method_call"
    if callee_kind == "Property":
        return "property_access"
    if callee_kind == "Function":
        return "function_call"

    return "type_hint"


def _map_on_kind(recv_value_kind: str | None) -> str | None:
    """Map receiver value_kind to display on_kind."""
    if recv_value_kind == "parameter":
        return "param"
    if recv_value_kind == "local":
        return "local"
    if recv_value_kind == "self":
        return "self"
    if recv_value_kind == "result":
        return "property"
    return recv_value_kind


def _trace_source_chain(runner: QueryRunner, value_node_id: str) -> list | None:
    """Trace the source chain for a result Value node.

    For property access results, follows the receiver chain to build
    a source chain showing what property is accessed on what object.

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


def _build_argument_info(runner: QueryRunner, call_id: str) -> list[ArgumentInfo]:
    """Build argument info list for a Call."""
    records = runner.execute(_Q_CALL_ARGUMENTS, call_id=call_id)
    infos: list[ArgumentInfo] = []
    for r in records:
        position = r.get("position")
        if position is None:
            continue

        param_fqn = r.get("parameter")
        # Derive param_name from param FQN: "Class::method().$name" -> "$name"
        param_name = None
        if param_fqn:
            if ".$" in param_fqn:
                param_name = param_fqn.rsplit(".", 1)[-1]
            elif "$" in param_fqn:
                param_name = "$" + param_fqn.rsplit("$", 1)[-1]

        # value_ref_symbol: the value's FQN if it's a proper symbol reference
        value_fqn = r.get("value_fqn")
        value_ref_symbol = None
        if value_fqn and "::" in value_fqn:
            value_ref_symbol = value_fqn

        # Trace source chain for result values
        source_chain = None
        value_kind = r.get("value_kind")
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


def _build_access_chain(
    runner: QueryRunner, call_id: str
) -> tuple[str | None, str | None]:
    """Build access chain and access chain symbol from a Call node's receiver.

    Resolves the receiver chain by following:
    - recv_value_kind == "parameter" -> "$paramName"
    - recv_value_kind == "local" -> "$localName"
    - recv_value_kind == "self" -> "$this"
    - recv_value_kind == "result" from property access -> "$this->propName"

    Returns:
        Tuple of (access_chain, access_chain_symbol).
    """
    rec = runner.execute_single(_Q_ACCESS_CHAIN_FULL, call_id=call_id)
    if not rec:
        return None, None

    recv_value_kind = rec.get("recv_value_kind")
    recv_name = rec.get("recv_name")
    src_call_kind = rec.get("src_call_kind")
    prop_fqn = rec.get("prop_fqn")
    prop_name = rec.get("prop_name")
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
        # Result of a property access -> build chain
        if src_call_kind == "access" and prop_name:
            member_name = prop_name.lstrip("$")
            # Determine the source receiver
            if src_recv_value_kind == "self" or src_recv_value_kind is None:
                chain = f"$this->{member_name}"
            elif src_recv_value_kind == "parameter":
                chain = f"{src_recv_name}->{member_name}"
            elif src_recv_value_kind == "local":
                chain = f"{src_recv_name}->{member_name}"
            else:
                chain = f"$this->{member_name}"
            return chain, prop_fqn
        # Result of a method call -> show as method()
        if src_call_kind in ("method", "method_static") and prop_name:
            member_name = prop_name
            if src_recv_value_kind == "self" or src_recv_value_kind is None:
                chain = f"$this->{member_name}()"
            elif src_recv_value_kind == "parameter":
                chain = f"{src_recv_name}->{member_name}()"
            elif src_recv_value_kind == "local":
                chain = f"{src_recv_name}->{member_name}()"
            else:
                chain = f"$this->{member_name}()"
            return chain, prop_fqn

    return None, None


def build_generic_used_by(
    runner: QueryRunner,
    target: NodeData,
    max_depth: int = 1,
    limit: int = 100,
    with_imports: bool = False,
) -> list[ContextEntry]:
    """Build USED BY section for Method/Function nodes using method-level format.

    Produces entries with member_ref (not ref_type) matching the reference
    implementation's generic handler.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        target: NodeData for the target symbol.
        max_depth: Maximum depth for recursive expansion.
        limit: Maximum number of entries.
        with_imports: Include import references.

    Returns:
        List of ContextEntry objects.
    """
    visited: set[str] = {target.node_id}
    count = [0]

    def build_tree(
        current_id: str,
        current_depth: int,
        branch_visited: set[str] | None = None,
    ) -> list[ContextEntry]:
        if current_depth > max_depth or count[0] >= limit:
            return []

        # Per-branch visited set for cycle prevention in recursive depth (R7)
        if branch_visited is None:
            branch_visited = set()

        records = runner.execute(_Q_INCOMING_USAGES, target_id=current_id)

        # Pass 1: collect entries
        entries: list[ContextEntry] = []
        expanded: set[str] = set()

        for r in records:
            source_id = r["source_id"]
            if source_id in visited:
                continue

            if count[0] >= limit:
                break

            count[0] += 1
            visited.add(source_id)

            source_fqn = r.get("source_fqn") or ""
            source_kind = r.get("source_kind")
            source_file = r.get("source_file")
            source_start_line = r.get("source_start_line")
            source_signature = r.get("source_signature")
            edge_file = r.get("edge_file")
            edge_line = r.get("edge_line")

            # Location: prefer edge location, fall back to source node
            file = edge_file or source_file
            line = edge_line if edge_line is not None else source_start_line

            # Try to find a Call node for this usage
            call_id = None
            call_kind = None
            callee_kind = None
            reference_type = None
            access_chain = None
            access_chain_symbol = None
            arguments: list[ArgumentInfo] = []

            if True:
                # Strategy 1: Find Call within source that targets the current node
                call_rec = runner.execute_single(
                    _Q_FIND_CALL_BY_TARGET,
                    source_id=source_id,
                    target_id=current_id,
                )
                # Strategy 2: Find Call targeting current node in same file
                if not call_rec and file is not None and line is not None:
                    call_rec = runner.execute_single(
                        _Q_FIND_CALL_TO_TARGET,
                        target_id=current_id,
                        file=file,
                        line=int(line),
                    )
                # Strategy 3: Constructor fallback
                if not call_rec:
                    call_rec = runner.execute_single(
                        _Q_FIND_CONSTRUCTOR_CALL,
                        source_id=source_id,
                        target_id=current_id,
                    )
                if call_rec:
                    call_id = call_rec.get("call_id")
                    call_kind = call_rec.get("call_kind")
                    callee_kind = call_rec.get("callee_kind")

                    # Resolve reference type from Call node's call_kind
                    reference_type = _resolve_reference_type(
                        r.get("edge_type", "USES"),
                        call_kind, callee_kind, source_kind,
                    )

                    # Resolve access_chain and access_chain_symbol from receiver
                    if call_id:
                        access_chain, access_chain_symbol = _build_access_chain(
                            runner, call_id
                        )

                    # Build arguments
                    if call_id:
                        arguments = _build_argument_info(runner, call_id)

            if not reference_type:
                reference_type = "type_hint"

            # For member_ref: at depth 1 use the original target,
            # at deeper depths resolve the current_id's node for proper FQN/name
            if current_id == target.node_id:
                mr_name = _display_name(target.kind, target.name)
                mr_fqn = target.fqn
                mr_kind = target.kind
            else:
                # Fetch the actual node for the recursive target
                cur_rec = runner.execute_single(
                    "MATCH (n:Node {node_id: $node_id}) "
                    "RETURN n.kind AS kind, n.fqn AS fqn, n.name AS name",
                    node_id=current_id,
                )
                if cur_rec:
                    cur_fqn = cur_rec.get("fqn", current_id)
                    cur_name = cur_rec.get("name", "")
                    cur_kind = cur_rec.get("kind", "")
                    mr_name = _display_name(cur_kind, cur_name)
                    mr_fqn = cur_fqn
                    mr_kind = cur_kind
                else:
                    mr_name = _display_name(target.kind, target.name)
                    mr_fqn = current_id
                    mr_kind = target.kind

            # Build member_ref for the entry
            member_ref = MemberRef(
                target_name=mr_name,
                target_fqn=mr_fqn,
                target_kind=mr_kind,
                file=file,
                line=line,
                reference_type=reference_type,
                access_chain=access_chain,
                access_chain_symbol=access_chain_symbol,
            )

            # Determine entry FQN/kind — for source nodes
            entry_fqn = source_fqn

            # If source is a Method, format FQN with ()
            if source_kind == "Method" and not entry_fqn.endswith("()"):
                entry_fqn = entry_fqn + "()"

            entry = ContextEntry(
                depth=current_depth,
                node_id=source_id,
                fqn=entry_fqn,
                kind=source_kind,
                file=file,
                line=line,
                signature=source_signature,
                children=[],
                member_ref=member_ref,
                arguments=arguments,
            )
            entries.append(entry)

        # R2: Sort by (file, line)
        entries.sort(
            key=lambda e: (e.file or "", e.line if e.line is not None else 0)
        )

        # Pass 2: expand children (R7/R8)
        if current_depth < max_depth:
            for entry in entries:
                if entry.node_id in expanded:
                    continue
                expanded.add(entry.node_id)

                # R8: Only chainable reference types expand
                ref_type = entry.member_ref.reference_type if entry.member_ref else None
                if ref_type not in CHAINABLE_REFERENCE_TYPES:
                    continue

                # R7: Resolve containing method for recursive depth
                resolve_id = entry.node_id
                node_kind_rec = runner.execute_single(
                    _Q_NODE_KIND, node_id=resolve_id
                )
                node_kind = node_kind_rec["kind"] if node_kind_rec else None

                method_id = None
                if node_kind in ("Method", "Function"):
                    method_id = resolve_id
                else:
                    method_rec = runner.execute_single(
                        _Q_CONTAINING_METHOD, node_id=resolve_id
                    )
                    if method_rec:
                        method_id = method_rec.get("method_id")

                if method_id and method_id not in branch_visited:
                    child_branch_visited = branch_visited | {method_id}
                    entry.children = build_tree(
                        method_id, current_depth + 1, child_branch_visited
                    )

        return entries

    return build_tree(target.node_id, 1)


def _display_name(kind: str, name: str) -> str:
    """Format display name for member_ref."""
    if kind in ("Method", "Function"):
        return f"{name}()" if not name.endswith("()") else name
    if kind == "Property":
        return name if name.startswith("$") else f"${name}"
    return name
