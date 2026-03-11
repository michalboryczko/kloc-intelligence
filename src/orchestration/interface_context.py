"""Interface context orchestrators: build USED BY and USES trees for Interface nodes.

Ported from kloc-cli's interface_context.py logic. Each public function fetches
pre-resolved data from Neo4j (via batch query functions) then applies pure logic
to produce lists of ContextEntry objects.

Key design rules:
- All Neo4j access is via batch fetch functions or targeted per-item queries.
- Contract relevance filtering: injection points are skipped if they don't call
  any of the interface's contract methods.
- Injection point calls have crossed_from set to the containing class FQN.
- Multi-site dedup: same callee from multiple methods -> sites array.
- Depth-2 under [implements]: override methods (filtered to contract methods).
- Depth-2 under [property_type]: injection point calls via Q6.
- Depth-3 under [property_type]: callers of containing method.
- All line numbers are 0-based; conversion to 1-based happens in OutputEntry.
"""

from __future__ import annotations

from ..db.query_runner import QueryRunner
from ..db.queries.context_interface import (
    Q5_CONTRACT_RELEVANCE,
    Q6_INJECTION_POINT_CALLS,
    Q7_INTERFACE_METHODS,
    Q8_IMPLEMENTS_DEPTH2,
    Q1_DIRECT_IMPLEMENTORS,
    fetch_interface_used_by_data,
    fetch_interface_uses_data,
)
from ..logic.graph_helpers import format_method_fqn
from ..models.node import NodeData
from ..models.results import ContextEntry
from .class_context import build_caller_chain_for_method, _build_call_arguments, build_class_uses_recursive

# Priority order for USES entries
USES_PRIORITY: dict[str, int] = {
    "extends": 0,
    "implements": 1,
    "property_type": 2,
    "parameter_type": 3,
    "return_type": 3,
    "instantiation": 4,
    "type_hint": 5,
}


# =============================================================================
# Internal helpers
# =============================================================================


def entry_targets_contract_method(
    entry: ContextEntry,
    contract_method_names: list[str],
) -> bool:
    """Return True if the entry's callee matches a contract method name.

    Used to filter depth-2 override method entries under [implements] to only
    those that override one of the interface's declared methods.

    Args:
        entry: A ContextEntry (typically a method override at depth 2).
        contract_method_names: List of method names defined on the interface.

    Returns:
        True if entry.fqn ends with a contract method name (e.g. '::save()').
    """
    if not contract_method_names:
        return True  # No contract methods means no filter
    fqn = entry.fqn or ""
    # Strip trailing () for comparison
    bare_fqn = fqn.rstrip("()")
    method_name = bare_fqn.rsplit("::", 1)[-1] if "::" in bare_fqn else bare_fqn
    return method_name in contract_method_names


def _check_contract_relevance(
    runner: QueryRunner,
    prop_id: str,
    prop_fqn: str,
    contract_method_names: list[str],
) -> bool:
    """Check if a property injection point calls any contract methods.

    Args:
        runner: Active QueryRunner.
        prop_id: Node ID of the property.
        prop_fqn: FQN of the property.
        contract_method_names: List of interface method names.

    Returns:
        True if the property's containing class calls any contract method
        through this property.
    """
    if not contract_method_names:
        return True

    records = runner.execute(
        Q5_CONTRACT_RELEVANCE,
        prop_id=prop_id,
        prop_fqn=prop_fqn,
        contract_method_names=contract_method_names,
    )
    if not records:
        return False
    return bool(records[0]["calls_contract"])


_Q_EXTENDS_FROM_INTERFACE = """
MATCH (iface:Node {node_id: $iface_id})<-[:EXTENDS]-(child)
WHERE child.kind = 'Interface'
RETURN child.node_id AS id, child.fqn AS fqn, child.kind AS kind,
       child.file AS file, child.start_line AS start_line
ORDER BY child.fqn
"""


def _build_interface_extends_depth2(
    runner: QueryRunner,
    child_node_id: str,
    contract_method_names: list[str],
    max_depth: int,
) -> list[ContextEntry]:
    """Build depth-2 entries for an interface extends child.

    Returns own methods of the child interface (filtered to contract methods),
    plus interfaces that extend this child interface.

    Args:
        runner: Active QueryRunner.
        child_node_id: Node ID of the child interface.
        contract_method_names: Interface contract method names (for filtering).
        max_depth: Maximum depth; depth-2 expansion only if max_depth >= 2.

    Returns:
        List of ContextEntry objects for the child interface's own methods
        and deeper extends children.
    """
    if max_depth < 2:
        return []

    entries: list[ContextEntry] = []

    # Own methods — show all methods of the child interface (no contract filter)
    records = runner.execute(Q7_INTERFACE_METHODS, id=child_node_id)
    for r in records:
        fqn = r.get("fqn", "")
        entry = ContextEntry(
            depth=2,
            node_id=r["id"],
            fqn=format_method_fqn(fqn, "Method"),
            kind="Method",
            file=r.get("file"),
            line=r.get("start_line"),
            signature=r.get("signature"),
            ref_type="own_method",
        )
        entries.append(entry)

    # Interfaces that extend this child interface
    ext_records = runner.execute(_Q_EXTENDS_FROM_INTERFACE, iface_id=child_node_id)
    for r in ext_records:
        entry = ContextEntry(
            depth=2,
            node_id=r["id"],
            fqn=r["fqn"],
            kind=r.get("kind"),
            file=r.get("file"),
            line=r.get("start_line"),
            ref_type="extends",
        )
        entries.append(entry)

    return entries


# =============================================================================
# build_interface_used_by
# =============================================================================


def build_interface_used_by(
    runner: QueryRunner,
    node: NodeData,
    max_depth: int = 1,
    limit: int = 100,
) -> list[ContextEntry]:
    """Build the USED BY section for an Interface node context.

    Flow:
    1. Collect direct implementors (Q1) -> [implements] entries
    2. Collect extends children (Q2) -> [extends] entries
    3. Get contract method names (Q4)
    4. Get injection points (Q3) -> [property_type] entries
       - For each, check contract relevance (Q5) before including
       - Skip irrelevant consumers
    5. Depth 2 under [implements]: override methods via Q8 (filtered to contract methods)
    6. Depth 2 under [property_type]: injection point calls via Q6
       - Multi-site dedup: same callee from multiple methods -> sites array
       - crossed_from: containing class FQN
    7. Depth 3 under [property_type]: callers of containing method
       - If no callers: show containing method itself with ref_type="caller"
    8. Depth 2 under [extends]: own methods + deeper extends
    9. Sort: [implements] first, [extends] second, [property_type] third
    10. Apply limit

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node: NodeData for the target interface.
        max_depth: Maximum depth for expansion.
        limit: Maximum number of top-level entries to return.

    Returns:
        Ordered list of ContextEntry objects representing who uses the interface.
    """
    data = fetch_interface_used_by_data(runner, node.node_id)

    implementors: list[dict] = data["implementors"]
    extends_children: list[dict] = data["extends_children"]
    injection_points: list[dict] = data["injection_points"]
    contract_method_names: list[str] = data["contract_methods"]

    # ------------------------------------------------------------------
    # 1. Implements entries
    # ------------------------------------------------------------------
    implements_entries: list[ContextEntry] = []
    for impl in implementors:
        entry = ContextEntry(
            depth=1,
            node_id=impl["id"],
            fqn=impl["fqn"],
            kind=impl.get("kind"),
            file=impl.get("file"),
            line=impl.get("start_line"),
            ref_type="implements",
        )
        implements_entries.append(entry)

    # ------------------------------------------------------------------
    # 2. Extends entries
    # ------------------------------------------------------------------
    extends_entries: list[ContextEntry] = []
    for child in extends_children:
        entry = ContextEntry(
            depth=1,
            node_id=child["id"],
            fqn=child["fqn"],
            kind=child.get("kind"),
            file=child.get("file"),
            line=child.get("start_line"),
            ref_type="extends",
        )
        extends_entries.append(entry)

    # ------------------------------------------------------------------
    # 3-4. Property type injection points (with contract relevance check)
    # ------------------------------------------------------------------
    # Split into direct (via_fqn matches target) and transitive (via child interface).
    # kloc-cli only includes transitive injection points if there are no direct ones.
    direct_pts: list[dict] = []
    transitive_pts: list[dict] = []
    for pt in injection_points:
        via_fqn = pt.get("via_fqn")
        if via_fqn == node.fqn or via_fqn is None:
            direct_pts.append(pt)
        else:
            transitive_pts.append(pt)

    # Use direct injection points; fall back to transitive only if none found
    active_pts = direct_pts if direct_pts else transitive_pts

    property_type_entries: list[ContextEntry] = []
    seen_props: set[str] = set()
    for pt in active_pts:
        prop_id = pt["prop_id"]
        prop_fqn = pt["prop_fqn"]

        # Deduplicate injection points by prop_id
        if prop_id in seen_props:
            continue
        seen_props.add(prop_id)

        # Skip if injection doesn't call any contract method
        if not _check_contract_relevance(
            runner, prop_id, prop_fqn, contract_method_names
        ):
            continue

        via_fqn = pt.get("via_fqn")
        # Only set via if it differs from the target interface
        via_value = via_fqn if via_fqn and via_fqn != node.fqn else None

        entry = ContextEntry(
            depth=1,
            node_id=prop_id,
            fqn=prop_fqn,
            kind="Property",
            file=pt.get("prop_file"),
            line=pt.get("prop_start_line"),
            ref_type="property_type",
            via=via_value,
        )
        property_type_entries.append(entry)

    # ------------------------------------------------------------------
    # Depth-2 expansions
    # ------------------------------------------------------------------
    if max_depth >= 2:
        # 5. Under [implements]: override methods (filtered to contract methods)
        #    Also: classes that extend the implementor
        _Q_EXTENDS_FROM_IMPL = """
        MATCH (impl:Node {node_id: $impl_id})<-[:EXTENDS*]-(child)
        RETURN child.node_id AS id, child.fqn AS fqn, child.kind AS kind,
               child.file AS file, child.start_line AS start_line
        ORDER BY child.fqn
        """
        for impl_entry in implements_entries:
            if not impl_entry.node_id:
                continue
            # Override methods — kloc-cli includes ALL overrides regardless of
            # which parent interface they come from (no contract method filter).
            records = runner.execute(Q8_IMPLEMENTS_DEPTH2, impl_id=impl_entry.node_id)
            for r in records:
                # Only include methods that have an override parent
                if not r.get("overrides_id"):
                    continue
                child = ContextEntry(
                    depth=2,
                    node_id=r["method_id"],
                    fqn=format_method_fqn(r.get("method_fqn", ""), "Method"),
                    kind="Method",
                    file=r.get("method_file"),
                    line=r.get("method_start_line"),
                    signature=r.get("method_signature"),
                    ref_type="override",
                )
                impl_entry.children.append(child)

            # Classes that extend this implementor
            ext_records = runner.execute(_Q_EXTENDS_FROM_IMPL, impl_id=impl_entry.node_id)
            for r in ext_records:
                child = ContextEntry(
                    depth=2,
                    node_id=r["id"],
                    fqn=r["fqn"],
                    kind=r.get("kind"),
                    file=r.get("file"),
                    line=r.get("start_line"),
                    ref_type="extends",
                )
                impl_entry.children.append(child)

        # 6. Under [property_type]: injection point calls
        for pt_entry in property_type_entries:
            if not pt_entry.node_id:
                continue

            injection_records = runner.execute(
                Q6_INJECTION_POINT_CALLS,
                property_id=pt_entry.node_id,
                property_fqn=pt_entry.fqn,
            )

            # Multi-site dedup: group by callee_id across methods
            # key: callee_id -> {entry, sites: [line, ...], method_ids: set}
            callee_map: dict[str, dict] = {}

            for r in injection_records:
                callee_id = r.get("callee_id")
                method_id = r.get("method_id")
                call_line = r.get("call_line")
                method_name = r.get("method_name", "")
                class_fqn = r.get("class_fqn", "")
                callee_name_raw = r.get("callee_name", "")

                if not callee_id:
                    continue

                # Filter: only include calls to contract methods
                if contract_method_names and callee_name_raw not in contract_method_names:
                    continue

                # Build site dict with method name and 0-based line
                site_entry = None
                if call_line is not None:
                    site_entry = {"method": method_name, "line": call_line}

                if callee_id not in callee_map:
                    callee_name = r.get("callee_name")
                    callee_kind = r.get("callee_kind", "")
                    if callee_kind == "Method":
                        callee_display = (callee_name + "()") if callee_name else None
                    else:
                        callee_display = callee_name

                    # Don't create sites yet — kloc-cli stores first call as the
                    # entry's line, and only creates sites lazily on duplicate.
                    callee_map[callee_id] = {
                        "node_id": method_id,
                        "method_fqn": r.get("method_fqn", ""),
                        "method_file": r.get("method_file"),
                        "callee_display": callee_display,
                        "callee_fqn": r.get("callee_fqn", ""),
                        "crossed_from": class_fqn,
                        "sites": [],
                        "method_ids": {method_id},
                        "first_line": call_line,
                        "call_id": r.get("call_id"),
                    }
                else:
                    # Duplicate callee — accumulate sites.
                    # kloc-cli behavior: when first duplicate is found, create
                    # retroactive first site with the CURRENT method's name (not
                    # the original method), then append the new site.
                    info = callee_map[callee_id]
                    if site_entry:
                        if not info["sites"]:
                            # Retroactive: create initial site from first_line
                            # with CURRENT method name (matches kloc-cli)
                            info["sites"].append({"method": method_name, "line": info["first_line"]})
                        info["sites"].append(site_entry)
                    if method_id not in info["method_ids"]:
                        info["method_ids"].add(method_id)

            # Build 'on' expression from property FQN
            prop_on = None
            if pt_entry.fqn and "::" in pt_entry.fqn:
                prop_short = pt_entry.fqn.rsplit("::", 1)[-1]
                if prop_short.startswith("$"):
                    prop_on = f"$this->{prop_short[1:]}"
                else:
                    prop_on = f"$this->{prop_short}"

            for callee_id, info in callee_map.items():
                sites = info["sites"]
                # FQN is the callee's FQN (the method being called), not the caller
                callee_fqn = info.get("callee_fqn", "")
                # Fetch arguments for the call
                call_arguments = []
                call_id = info.get("call_id")
                if call_id:
                    call_arguments = _build_call_arguments(runner, call_id)

                # Multi-site: when sites > 1, line is None (moved into sites array).
                # Single-site: line comes from the first/only site.
                is_multi_site = len(sites) > 1
                if is_multi_site:
                    entry_line = None
                elif sites:
                    first_site = sites[0]
                    entry_line = first_site.get("line") if isinstance(first_site, dict) else first_site
                else:
                    entry_line = info.get("first_line")

                child = ContextEntry(
                    depth=2,
                    node_id=info["node_id"],
                    fqn=format_method_fqn(callee_fqn, "Method"),
                    kind="Method",
                    file=info.get("method_file"),
                    line=entry_line,
                    ref_type="method_call",
                    callee=info["callee_display"],
                    on=prop_on,
                    on_kind="property" if prop_on else None,
                    crossed_from=info["crossed_from"],
                    sites=sites if is_multi_site else None,
                    arguments=call_arguments,
                )
                pt_entry.children.append(child)

                # 7. Depth 3: callers of the containing method
                if max_depth >= 3 and info["node_id"]:
                    visited_for_chain: set[str] = {info["node_id"]}
                    callers = build_caller_chain_for_method(
                        runner,
                        info["node_id"],
                        depth=3,
                        max_depth=max_depth,
                        visited=visited_for_chain,
                    )
                    if callers:
                        child.children = callers
                    else:
                        # No callers: show containing method itself
                        self_entry = ContextEntry(
                            depth=3,
                            node_id=info["node_id"],
                            fqn=format_method_fqn(info["method_fqn"], "Method"),
                            kind="Method",
                            ref_type="caller",
                        )
                        child.children = [self_entry]

        # 8. Under [extends]: own methods of child interface
        for ext_entry in extends_entries:
            if not ext_entry.node_id:
                continue
            ext_entry.children = _build_interface_extends_depth2(
                runner, ext_entry.node_id, contract_method_names, max_depth
            )

    # ------------------------------------------------------------------
    # Sort within each bucket by (file, line) for stable ordering
    # ------------------------------------------------------------------
    implements_entries.sort(
        key=lambda e: (e.file or "", e.line if e.line is not None else 0)
    )
    extends_entries.sort(
        key=lambda e: (e.file or "", e.line if e.line is not None else 0)
    )
    property_type_entries.sort(
        key=lambda e: (e.file or "", e.line if e.line is not None else 0)
    )

    # ------------------------------------------------------------------
    # 9. Combine in priority order: implements, extends, property_type
    # ------------------------------------------------------------------
    all_entries = implements_entries + extends_entries + property_type_entries

    # 10. Apply limit
    return all_entries[:limit]


# =============================================================================
# build_interface_uses
# =============================================================================


def build_interface_uses(
    runner: QueryRunner,
    node: NodeData,
    max_depth: int = 1,
    limit: int = 100,
    include_impl: bool = False,
) -> list[ContextEntry]:
    """Build the USES section for an Interface node context.

    Flow:
    1. Get extends parent (Q10) -> [extends] entry
    2. Get signature types from own methods (Q9):
       - Return types -> [return_type]
       - Parameter types -> [parameter_type] (wins over return_type for same target)
    3. If include_impl: get implementors (Q1) -> [implements] entries
    4. Sort: extends first, implements, then parameter_type/return_type
    5. Apply limit

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node: NodeData for the target interface.
        max_depth: Maximum depth (currently unused; future use).
        limit: Maximum number of entries to return.
        include_impl: If True, include implementors in the USES section.

    Returns:
        Priority-ordered list of ContextEntry objects for USES section.
    """
    data = fetch_interface_uses_data(runner, node.node_id)

    signature_types: list[dict] = data["signature_types"]
    extends_parent: list[dict] = data["extends_parent"]

    # ------------------------------------------------------------------
    # 1. Extends parent entry
    # ------------------------------------------------------------------
    extends_entries: list[ContextEntry] = []
    for parent in extends_parent:
        entry = ContextEntry(
            depth=1,
            node_id=parent["id"],
            fqn=parent["fqn"],
            kind=parent.get("kind"),
            # For extends in USES, file/line is the source interface (where the
            # extends keyword is written), not the target parent.
            file=node.file,
            line=node.start_line,
            ref_type="extends",
        )
        extends_entries.append(entry)

    # ------------------------------------------------------------------
    # 2. Signature types: parameter_type wins over return_type per target
    # ------------------------------------------------------------------
    # Track best ref_type per target_id
    target_best: dict[str, dict] = {}

    for row in signature_types:
        # Process return type (lower priority than parameter_type)
        ret_id = row.get("ret_type_id")
        ret_fqn = row.get("ret_type_fqn")
        ret_kind = row.get("ret_type_kind")
        if ret_id and ret_fqn:
            existing = target_best.get(ret_id)
            if existing is None:
                target_best[ret_id] = {
                    "target_id": ret_id,
                    "target_fqn": ret_fqn,
                    "target_kind": ret_kind,
                    "ref_type": "return_type",
                    "file": row.get("method_file"),
                    "line": row.get("method_line"),
                }
            # return_type never upgrades an existing entry — parameter_type
            # always wins over return_type for the same target.

        # Process parameter type (higher priority — always wins over return_type)
        param_id = row.get("param_type_id")
        param_fqn = row.get("param_type_fqn")
        param_kind = row.get("param_type_kind")
        if param_id and param_fqn:
            existing = target_best.get(param_id)
            if existing is None:
                target_best[param_id] = {
                    "target_id": param_id,
                    "target_fqn": param_fqn,
                    "target_kind": param_kind,
                    "ref_type": "parameter_type",
                    "file": row.get("method_file"),
                    "line": row.get("method_line"),
                }
            else:
                # parameter_type always beats return_type for the same target
                if existing["ref_type"] == "return_type":
                    existing["ref_type"] = "parameter_type"
                    existing["file"] = row.get("method_file")
                    existing["line"] = row.get("method_line")

    type_entries: list[ContextEntry] = []
    for td in target_best.values():
        type_entries.append(ContextEntry(
            depth=1,
            node_id=td["target_id"],
            fqn=td["target_fqn"],
            kind=td.get("target_kind"),
            file=td.get("file"),
            line=td.get("line"),
            ref_type=td["ref_type"],
        ))

    # ------------------------------------------------------------------
    # 3. Optional: implementors
    # ------------------------------------------------------------------
    impl_entries: list[ContextEntry] = []
    if include_impl:
        records = runner.execute(Q1_DIRECT_IMPLEMENTORS, id=node.node_id)
        for r in records:
            impl_entries.append(ContextEntry(
                depth=1,
                node_id=r["id"],
                fqn=r["fqn"],
                kind=r.get("kind"),
                file=r.get("file"),
                line=r.get("start_line"),
                ref_type="implements",
            ))

    # ------------------------------------------------------------------
    # Depth-2+: recursive class USES expansion for each type dep
    # ------------------------------------------------------------------
    if max_depth >= 2:
        parent_visited: set[str] = {node.node_id}
        for entry in extends_entries + impl_entries + type_entries:
            if entry.node_id and entry.kind in ("Class", "Interface", "Trait", "Enum"):
                entry.children = build_class_uses_recursive(
                    runner, entry.node_id, 2, max_depth, limit,
                    visited=set(parent_visited),
                )

    # ------------------------------------------------------------------
    # 4. Combine and sort by USES priority
    # ------------------------------------------------------------------
    all_entries = extends_entries + impl_entries + type_entries
    all_entries.sort(
        key=lambda e: (
            USES_PRIORITY.get(e.ref_type or "", 99),
            e.file or "",
            e.line or 0,
        )
    )

    # 5. Apply limit
    return all_entries[:limit]
