"""Property context orchestrators: build USED BY and USES trees for Property nodes.

Ported from kloc-cli's property_context.py. Handles property access grouping,
deduplication, depth expansion, and promoted constructor parameter tracing.

Key design rules:
- Groups property accesses by containing method with (xN) dedup.
- Depth 2: traces result Values via build_value_consumer_chain.
- ISSUE-O: Filters constructor args to only the one matching queried property.
- ISSUE-D: Deduplicates children by (fqn, file, line).
- Caller chain integration via callback function.
- All line numbers are 0-based.
"""

from __future__ import annotations

from typing import Callable

from ..db.query_runner import QueryRunner
from ..db.queries.context_property import (
    Q3_PARAM_CALLERS,
    fetch_property_used_by_data,
    fetch_property_uses_data,
)
from ..logic.graph_helpers import member_display_name
from ..models.results import ContextEntry, MemberRef, ArgumentInfo
from .value_context import (
    build_value_consumer_chain,
    build_value_source_chain,
)


# =============================================================================
# Internal helpers
# =============================================================================


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


def _build_on_display(
    receiver_names: list[tuple[str, str]],
    property_node_name: str,
    property_fqn: str,
) -> str | None:
    """Build on display string from receiver_names list.

    Args:
        receiver_names: List of (name, kind) tuples for receivers.
        property_node_name: Name of the property.
        property_fqn: FQN of the property.

    Returns:
        Display string or None.
    """
    if not receiver_names:
        return None

    parts = []
    for rname, rkind in receiver_names:
        if rkind == "self":
            prop_name = property_node_name
            if not prop_name.startswith("$"):
                prop_name = "$" + prop_name
            parts.append(f"$this->{prop_name.lstrip('$')} ({property_fqn})")
        else:
            parts.append(rname)
    return ", ".join(parts)


# =============================================================================
# build_property_uses
# =============================================================================


def build_property_uses(
    runner: QueryRunner,
    property_id: str,
    property_name: str,
    property_fqn: str,
    depth: int,
    max_depth: int,
    limit: int,
) -> list[ContextEntry]:
    """Build USES chain for a Property node.

    For promoted constructor properties: follows assigned_from edge to
    Value(parameter), then traces callers via argument edges. Shows only
    the argument matching this property (filtered), not all constructor args.

    Args:
        runner: Active QueryRunner.
        property_id: Node ID of the property.
        property_name: Name of the property.
        property_fqn: FQN of the property.
        depth: Current depth level.
        max_depth: Maximum depth to expand.
        limit: Maximum number of entries.

    Returns:
        List of ContextEntry representing sources of the property's value.
    """
    if depth > max_depth:
        return []

    data = fetch_property_uses_data(runner, property_id)
    promoted_params: list[dict] = data["promoted_params"]

    visited: set[str] = set()

    # Check for promoted parameter (assigned_from -> Value(parameter))
    for param_rec in promoted_params:
        param_fqn = param_rec.get("param_fqn")
        param_value_kind = param_rec.get("param_value_kind")
        if param_fqn and param_value_kind == "parameter":
            return build_property_callers_filtered(
                runner, param_fqn, property_name, property_fqn,
                depth, max_depth, limit, visited,
            )

    # No promoted parameter found
    return []


# =============================================================================
# build_property_callers_filtered
# =============================================================================


def build_property_callers_filtered(
    runner: QueryRunner,
    param_fqn: str,
    property_name: str,
    property_fqn: str,
    depth: int,
    max_depth: int,
    limit: int,
    visited: set[str],
) -> list[ContextEntry]:
    """Find callers of a constructor parameter, showing only the matching argument.

    Instead of showing all N constructor arguments, filters to show only
    the argument that maps to the queried property's parameter.

    Args:
        runner: Active QueryRunner.
        param_fqn: FQN of the parameter Value.
        property_name: Name of the property.
        property_fqn: FQN of the property.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        visited: Visited set for cycle detection.

    Returns:
        List of ContextEntry representing callers with filtered arguments.
    """
    records = runner.execute(Q3_PARAM_CALLERS, param_fqn=param_fqn)
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

        # Build filtered argument info for just this property's parameter
        filtered_args: list[ArgumentInfo] = []
        position = rec.get("position")
        if position is not None:
            filtered_args.append(ArgumentInfo(
                position=int(position),
                value_expr=rec.get("expression"),
                value_source=rec.get("value_kind"),
                value_type=rec.get("value_type"),
                value_ref_symbol=rec.get("value_fqn"),
                param_fqn=param_fqn,
            ))

        entry = ContextEntry(
            depth=depth,
            node_id=scope_id or call_id,
            fqn=scope_fqn,
            kind=scope_kind,
            file=call_file,
            line=call_line,
            signature=scope_signature,
            children=[],
            arguments=filtered_args,
            crossed_from=param_fqn,
        )

        # Trace the caller's argument Value source at depth+1
        if depth < max_depth and caller_value_id and caller_value_id not in visited:
            child_entries = build_value_source_chain(
                runner, caller_value_id, depth + 1, max_depth, limit, visited
            )
            entry.children.extend(child_entries)

        entries.append(entry)

    # Sort by (file, line)
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries


# =============================================================================
# build_property_used_by
# =============================================================================


def build_property_used_by(
    runner: QueryRunner,
    property_id: str,
    property_name: str,
    property_fqn: str,
    depth: int,
    max_depth: int,
    limit: int,
    caller_chain_fn: Callable | None = None,
) -> list[ContextEntry]:
    """Build USED BY chain for a Property node.

    Groups property accesses by containing method with (xN) dedup.
    For service properties: shows access -> method_call chain at depth 2.
    For entity properties: groups by method with (xN) counts.

    Args:
        runner: Active QueryRunner.
        property_id: Property node ID.
        property_name: Property name.
        property_fqn: Property FQN.
        depth: Current depth level.
        max_depth: Maximum depth.
        limit: Maximum entries.
        caller_chain_fn: Optional callback for building caller chain at depth+2.
            Signature: (method_id, depth, max_depth) -> list[ContextEntry]

    Returns:
        List of ContextEntry representing who accesses the property.
    """
    if depth > max_depth:
        return []

    data = fetch_property_used_by_data(runner, property_id)
    calls: list[dict] = data["calls"]

    # Group accesses by containing method (scope_id)
    method_groups: dict[str, list[dict]] = {}
    for rec in calls:
        scope_id = rec.get("scope_id")
        if not scope_id:
            continue
        if scope_id not in method_groups:
            method_groups[scope_id] = []
        method_groups[scope_id].append(rec)

    entries: list[ContextEntry] = []
    visited: set[str] = set()

    for scope_id, scope_calls in method_groups.items():
        if len(entries) >= limit:
            break

        # Use first call for representative info
        first = scope_calls[0]
        scope_fqn = first.get("scope_fqn") or ""
        scope_kind = first.get("scope_kind")
        scope_signature = first.get("scope_signature")
        first_file = first.get("call_file")
        first_line = first.get("call_line")

        # Collect unique receivers across all accesses in this method
        receiver_names: list[tuple[str, str]] = []
        for rec in scope_calls:
            recv_value_kind = rec.get("recv_value_kind")
            recv_name = rec.get("recv_name")
            recv_prop_fqn = rec.get("recv_prop_fqn")

            if recv_value_kind == "self":
                key = ("$this", "self")
                if key not in receiver_names:
                    receiver_names.append(key)
            elif recv_value_kind in ("local", "parameter"):
                rkind = "local" if recv_value_kind == "local" else "param"
                if recv_name and (recv_name, rkind) not in receiver_names:
                    receiver_names.append((recv_name, rkind))
            elif recv_value_kind == "result":
                # Chain access: build display like "$customer->address"
                recv_prop_name = rec.get("recv_prop_name")
                chain_recv_name = rec.get("chain_recv_name")
                chain_recv_kind = rec.get("chain_recv_kind")
                if recv_prop_name:
                    prop_display = recv_prop_name.lstrip("$")
                    if chain_recv_kind == "self" or (chain_recv_kind is None and chain_recv_name is None):
                        # Self access: $this->address
                        chain_display = f"$this->{prop_display}"
                    elif chain_recv_name:
                        chain_display = f"{chain_recv_name}->{prop_display}"
                    else:
                        chain_display = recv_prop_fqn or prop_display
                    if (chain_display, "property") not in receiver_names:
                        receiver_names.append((chain_display, "property"))
                elif recv_prop_fqn:
                    if (recv_prop_fqn, "property") not in receiver_names:
                        receiver_names.append((recv_prop_fqn, "property"))
            elif recv_value_kind is None:
                # No receiver edge: check call_kind for implicit $this
                call_kind = rec.get("call_kind")
                if call_kind in ("access", "method", "method_static"):
                    key = ("$this", "self")
                    if key not in receiver_names:
                        receiver_names.append(key)

        # Build sites for (xN) dedup
        access_count = len(scope_calls)
        sites = None
        if access_count > 1:
            sites = []
            for rec in scope_calls:
                site_line = rec.get("call_line")
                sites.append({"method": scope_fqn, "line": site_line})

        # Build on display
        on_display = _build_on_display(receiver_names, property_name, property_fqn)

        # Determine on_kind
        on_kind = None
        if receiver_names:
            first_rkind = receiver_names[0][1]
            on_kind = "property" if first_rkind == "self" else first_rkind

        # Reference type
        ref_type = "property_access"

        # Build member_ref
        prop_display = member_display_name("Property", property_name)
        member_ref = MemberRef(
            target_name=prop_display,
            target_fqn=property_fqn,
            target_kind="Property",
            file=first_file,
            line=first_line,
            reference_type=ref_type,
            on_kind=on_kind if not receiver_names else receiver_names[0][1],
        )

        entry = ContextEntry(
            depth=depth,
            node_id=scope_id,
            fqn=scope_fqn,
            kind=scope_kind,
            file=first_file,
            line=first_line,
            signature=scope_signature,
            children=[],
            member_ref=member_ref,
            ref_type=ref_type,
            callee=prop_display,
            on=on_display,
            on_kind=on_kind,
            sites=sites,
        )

        # Depth 2: trace result Values of each access
        if depth < max_depth:
            property_result_value_ids: set[str] = set()
            for rec in scope_calls:
                result_id = rec.get("result_id")
                if result_id:
                    property_result_value_ids.add(result_id)
                if result_id and result_id not in visited:
                    child_entries = build_value_consumer_chain(
                        runner, result_id, depth + 1, max_depth, limit, visited
                    )
                    entry.children.extend(child_entries)

            # ISSUE-D: Deduplicate children by (fqn, file, line)
            seen_children: set[tuple] = set()
            deduped_children: list[ContextEntry] = []
            for child in entry.children:
                key = (child.fqn, child.file, child.line)
                if key not in seen_children:
                    seen_children.add(key)
                    deduped_children.append(child)
            entry.children = deduped_children

            # ISSUE-O: Filter constructor/method args to only the matching property
            prop_name_bare = property_name.lstrip("$")
            for child_entry in entry.children:
                if child_entry.arguments:
                    filtered_args = []
                    for arg in child_entry.arguments:
                        if arg.value_expr and arg.value_expr.endswith(
                            f"->{prop_name_bare}"
                        ):
                            filtered_args.append(arg)
                        elif arg.value_expr and arg.value_expr.endswith(
                            f"->{property_name}"
                        ):
                            filtered_args.append(arg)
                    # Only apply filter if we found matches
                    if filtered_args:
                        child_entry.arguments = filtered_args

            # Caller chain integration
            if caller_chain_fn:
                if entry.children and depth + 1 < max_depth:
                    caller_entries = caller_chain_fn(
                        scope_id, depth + 2, max_depth
                    )
                    if caller_entries:
                        for child in entry.children:
                            child.children = caller_entries
                elif not entry.children:
                    caller_entries = caller_chain_fn(
                        scope_id, depth + 1, max_depth
                    )
                    if caller_entries:
                        entry.children = caller_entries

        entries.append(entry)

    # Sort by (file, line)
    entries.sort(key=lambda e: (e.file or "", e.line if e.line is not None else 0))
    return entries
