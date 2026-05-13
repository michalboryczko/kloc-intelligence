"""Import symfony-kloc.json (v3.0) into Neo4j as a four-label flow graph.

The v3 importer reconciles ``:Flow``, ``:Message``, ``:Event`` and
``:HttpClient`` node sets (MERGE upsert + diff-delete) and bulk-replaces the
structural edges (``FLOW_ENTRY``, ``FLOW_ENTRY_CLASS``, ``EMITS``,
``USES_HTTP_CLIENT``, ``HANDLED_BY``, ``OF_TYPE``) on every run.

Idempotency contract:
  - Re-importing the same JSON is a no-op for :Flow enrichment properties
    (``.explanation``, ``.explain_model``, ``.explain_at``). They are never
    overwritten on upsert.
  - Removing a flow from the JSON deletes the :Flow node AND its Qdrant points
    in ``flow_explain_embeddings`` (filter-delete by ``flow_id`` — collection
    is NEVER dropped).
  - Legacy ``FLOW_TRIGGERS`` edges (v2) are unconditionally swept every run.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .connection import Neo4jConnection

logger = logging.getLogger(__name__)

DEFAULT_FLOW_NAMESPACES: tuple[str, ...] = ("App\\",)
FLOW_NAMESPACES_ENV_VAR = "KLOC_FLOW_NAMESPACES"

FLOW_PRESERVED_PROPS = frozenset({"explanation", "explain_model", "explain_at"})


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def load_symfony_kloc(path: str | Path) -> dict:
    """Load and parse a symfony-kloc.json file."""
    with open(path) as f:
        return json.load(f)


def load_flow_namespaces() -> tuple[str, ...]:
    """Load the :Flow namespace allow-list from ``KLOC_FLOW_NAMESPACES``.

    Comma-separated list of FQN prefixes; whitespace around entries is
    stripped. Empty / unset value falls back to :data:`DEFAULT_FLOW_NAMESPACES`
    (``("App\\\\",)``). The filter applies only to ``:Flow`` entry FQNs;
    messages/events/http_clients are always imported regardless of namespace.
    """
    raw = os.environ.get(FLOW_NAMESPACES_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_FLOW_NAMESPACES
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts or DEFAULT_FLOW_NAMESPACES


def _flow_matches_namespaces(flow: dict, prefixes: tuple[str, ...]) -> bool:
    """True if the flow's entry FQN starts with any allow-listed prefix."""
    fqn = flow.get("entry", {}).get("fqn", "")
    return any(fqn.startswith(p) for p in prefixes)


def _derive_flow_name(flow_type: str, entry: dict) -> str:
    """Derive :Flow.name per spec D3a."""
    if flow_type == "http":
        methods = entry.get("http_methods", [])
        route = entry.get("route", "")
        return f"{' '.join(methods)} {route}".strip()
    if flow_type == "message":
        return entry.get("message", "").rsplit("\\", 1)[-1]
    if flow_type == "event":
        return entry.get("event", "").rsplit("\\", 1)[-1]
    if flow_type == "cli":
        return entry.get("command_name", "")
    return ""


def _flow_node_props(flow: dict) -> dict:
    flow_id = flow["id"]
    flow_type = flow.get("type", "")
    entry = flow.get("entry", {})
    props: dict = {
        "flow_id": flow_id,
        "type": flow_type,
        "name": _derive_flow_name(flow_type, entry),
        "entry_fqn": entry.get("fqn", ""),
        "entry_method": entry.get("method", ""),
    }
    if flow_type == "http":
        props["route"] = entry.get("route", "")
        props["http_methods"] = entry.get("http_methods", [])
    elif flow_type == "message":
        props["message_class"] = entry.get("message", "")
    elif flow_type == "event":
        props["event_name"] = entry.get("event", "")
    elif flow_type == "cli":
        props["command_name"] = entry.get("command_name", "")
    return props


def parse_v3(
    data: dict,
    namespaces: tuple[str, ...] | None = None,
) -> tuple[list[dict], list[dict], list[dict], list[dict], dict[str, list[dict]]]:
    """Parse v3 symfony-kloc.json into desired node sets and edge tuples.

    Returns ``(flows, messages, events, http_clients, edges)`` where ``edges``
    is keyed by edge-type string. Only :Flow entries whose entry FQN starts
    with one of the allow-listed namespace prefixes pass through;
    messages/events/http_clients are universal (no namespace filter).

    ``namespaces`` defaults to :func:`load_flow_namespaces` (env-driven,
    fallback ``("App\\\\",)``). Pass an explicit tuple to override for tests.
    """
    if namespaces is None:
        namespaces = load_flow_namespaces()

    raw_flows = data.get("flows", []) or []
    app_flows = [f for f in raw_flows if _flow_matches_namespaces(f, namespaces)]
    logger.info(
        "Parsing v3 flows: %d total, %d kept (namespaces=%s), %d filtered",
        len(raw_flows),
        len(app_flows),
        ",".join(namespaces),
        len(raw_flows) - len(app_flows),
    )

    flows: list[dict] = []
    edges: dict[str, list[dict]] = {
        "flow_entry": [],
        "flow_entry_class": [],
        "emits_message_flow": [],
        "emits_message_call": [],
        "emits_event_flow": [],
        "emits_event_call": [],
        "uses_http_flow": [],
        "uses_http_call": [],
        "handled_by_message": [],
        "handled_by_event": [],
        "of_type_message": [],
        "of_type_event": [],
        "of_type_http_client": [],
    }

    for flow in app_flows:
        flow_id = flow["id"]
        entry = flow.get("entry", {}) or {}
        flows.append(_flow_node_props(flow))

        method_node_id = entry.get("method_node_id")
        if method_node_id:
            edges["flow_entry"].append({"flow_id": flow_id, "method_node_id": method_node_id})
        class_node_id = entry.get("node_id")
        if class_node_id:
            edges["flow_entry_class"].append({"flow_id": flow_id, "class_node_id": class_node_id})

    # Messages
    messages: list[dict] = []
    for msg in data.get("messages", []) or []:
        msg_id = msg["id"]
        fqn = msg.get("dispatched_class", "")
        class_node_id = msg.get("dispatched_class_node_id")
        messages.append(
            {
                "id": msg_id,
                "fqn": fqn,
                "transports": list(msg.get("transports") or []),
            }
        )
        for src in msg.get("sources", []) or []:
            src_flow_id = src.get("flow_id", "")
            call_node_id = src.get("call_node_id", "")
            edges["emits_message_flow"].append(
                {
                    "flow_id": src_flow_id,
                    "message_id": msg_id,
                    "call_node_id": call_node_id,
                    "caller_method_fqn": src.get("caller_method_fqn", ""),
                    "caller_method_node_id": src.get("caller_method_node_id", ""),
                }
            )
            if call_node_id:
                edges["emits_message_call"].append(
                    {"call_node_id": call_node_id, "message_id": msg_id}
                )
        for tgt in msg.get("targets", []) or []:
            tgt_flow_id = tgt.get("flow_id", "")
            if tgt_flow_id:
                edges["handled_by_message"].append({"message_id": msg_id, "flow_id": tgt_flow_id})
        if class_node_id:
            edges["of_type_message"].append({"message_id": msg_id, "class_node_id": class_node_id})

    # Events
    events: list[dict] = []
    for evt in data.get("events", []) or []:
        evt_id = evt["id"]
        fqn = evt.get("dispatched_class", "")
        class_node_id = evt.get("dispatched_class_node_id")
        events.append({"id": evt_id, "fqn": fqn})
        for src in evt.get("sources", []) or []:
            src_flow_id = src.get("flow_id", "")
            call_node_id = src.get("call_node_id", "")
            edges["emits_event_flow"].append(
                {
                    "flow_id": src_flow_id,
                    "event_id": evt_id,
                    "call_node_id": call_node_id,
                    "caller_method_fqn": src.get("caller_method_fqn", ""),
                    "caller_method_node_id": src.get("caller_method_node_id", ""),
                }
            )
            if call_node_id:
                edges["emits_event_call"].append({"call_node_id": call_node_id, "event_id": evt_id})
        for tgt in evt.get("targets", []) or []:
            tgt_flow_id = tgt.get("flow_id", "")
            if tgt_flow_id:
                edges["handled_by_event"].append(
                    {
                        "event_id": evt_id,
                        "flow_id": tgt_flow_id,
                        "priority": int(tgt.get("priority", 0) or 0),
                    }
                )
        if class_node_id:
            edges["of_type_event"].append({"event_id": evt_id, "class_node_id": class_node_id})

    # HTTP clients
    http_clients: list[dict] = []
    for hc in data.get("http_clients", []) or []:
        hc_id = hc["id"]
        class_node_id = hc.get("class_node_id")
        http_clients.append(
            {
                "id": hc_id,
                "service_id": hc.get("service_id", ""),
                "class_fqn": hc.get("class", ""),
                "base_uri": hc.get("base_uri", ""),
            }
        )
        for src in hc.get("sources", []) or []:
            src_flow_id = src.get("flow_id", "")
            call_node_id = src.get("call_node_id", "")
            edges["uses_http_flow"].append(
                {
                    "flow_id": src_flow_id,
                    "http_client_id": hc_id,
                    "call_node_id": call_node_id,
                    "caller_method_fqn": src.get("caller_method_fqn", ""),
                    "caller_method_node_id": src.get("caller_method_node_id", ""),
                }
            )
            if call_node_id:
                edges["uses_http_call"].append(
                    {"call_node_id": call_node_id, "http_client_id": hc_id}
                )
        if class_node_id:
            edges["of_type_http_client"].append(
                {"http_client_id": hc_id, "class_node_id": class_node_id}
            )

    return flows, messages, events, http_clients, edges


# ---------------------------------------------------------------------------
# Reconcile helpers
# ---------------------------------------------------------------------------


def _label_safe(label: str) -> str:
    if not label.isidentifier():
        raise ValueError(f"unsafe Cypher label: {label!r}")
    return label


def _key_safe(key: str) -> str:
    if not key.isidentifier():
        raise ValueError(f"unsafe Cypher key: {key!r}")
    return key


def _build_set_clause(prop_keys: list[str], var: str = "n", payload: str = "props") -> str:
    """Build ``SET n.k1 = props.k1, n.k2 = props.k2, ...`` from a fixed key list.

    Empty input returns an empty string so callers can omit the SET section
    cleanly when nothing needs writing.
    """
    if not prop_keys:
        return ""
    parts = [f"{var}.{_key_safe(k)} = {payload}.{_key_safe(k)}" for k in prop_keys]
    return "SET " + ", ".join(parts)


def _reconcile_nodes(
    conn: Neo4jConnection,
    label: str,
    id_key: str,
    desired: list[dict],
    preserve_props: frozenset[str] | set[str] | None = None,
) -> tuple[int, list[str]]:
    """MERGE upsert ``desired`` and DETACH DELETE orphans of ``label``.

    Returns ``(upserted_count, deleted_ids)``. ``preserve_props`` are never set
    on upsert — used for :Flow to protect ``.explanation`` and friends.
    """
    label = _label_safe(label)
    id_key_s = _key_safe(id_key)
    preserve = set(preserve_props or ())

    desired_ids = [d[id_key] for d in desired]

    if desired:
        prop_keys: list[str] = []
        seen: set[str] = set()
        for d in desired:
            for k in d:
                if k == id_key:
                    continue
                if k in preserve:
                    continue
                if k not in seen:
                    seen.add(k)
                    prop_keys.append(k)
        set_clause = _build_set_clause(prop_keys, var="n", payload="props")
        cypher = (
            f"UNWIND $batch AS props "
            f"MERGE (n:{label} {{{id_key_s}: props.{id_key_s}}}) "
            f"{set_clause}"
        )
        with conn.session() as session:
            session.run(cypher, batch=desired)

    # Diff: find orphans (ids in DB not in desired set)
    with conn.session() as session:
        result = session.run(
            f"MATCH (n:{label}) WHERE NOT n.{id_key_s} IN $desired_ids RETURN n.{id_key_s} AS id",
            desired_ids=desired_ids,
        )
        deleted_ids = [record["id"] for record in result]

    if deleted_ids:
        with conn.session() as session:
            session.run(
                f"MATCH (n:{label}) WHERE n.{id_key_s} IN $ids DETACH DELETE n",
                ids=deleted_ids,
            )

    logger.info(
        ":%s reconcile: upserted=%d, deleted=%d",
        label,
        len(desired),
        len(deleted_ids),
    )
    return len(desired), deleted_ids


def _bulk_replace_edges(
    conn: Neo4jConnection,
    edge_label: str,
    from_label: str,
    from_id_key: str,
    to_label: str,
    to_id_key: str,
    edges: list[dict],
    from_field: str,
    to_field: str,
    prop_fields: list[str] | None = None,
) -> int:
    """Delete all edges of type ``edge_label`` between ``from_label``→``to_label``,
    then recreate them from the ``edges`` list.

    Edges with unresolved endpoints (missing node in Neo4j) are silently skipped;
    the count of created edges is returned and a WARNING is logged for the
    gap. The caller is responsible for the ``from_label``/``to_label`` mapping.
    """
    edge_label = _label_safe(edge_label)
    from_label = _label_safe(from_label)
    to_label = _label_safe(to_label)
    from_id_key_s = _key_safe(from_id_key)
    to_id_key_s = _key_safe(to_id_key)
    from_field_s = _key_safe(from_field)
    to_field_s = _key_safe(to_field)
    prop_fields = list(prop_fields or [])
    for p in prop_fields:
        _key_safe(p)

    with conn.session() as session:
        session.run(f"MATCH (:{from_label})-[r:{edge_label}]->(:{to_label}) DELETE r")

    if not edges:
        logger.info(
            "Edge %s (%s→%s): 0 created (no desired edges)",
            edge_label,
            from_label,
            to_label,
        )
        return 0

    set_clause = ""
    if prop_fields:
        parts = [f"r.{_key_safe(p)} = e.{_key_safe(p)}" for p in prop_fields]
        set_clause = "SET " + ", ".join(parts)

    cypher = (
        f"UNWIND $batch AS e "
        f"MATCH (a:{from_label} {{{from_id_key_s}: e.{from_field_s}}}) "
        f"MATCH (b:{to_label} {{{to_id_key_s}: e.{to_field_s}}}) "
        f"CREATE (a)-[r:{edge_label}]->(b) "
        f"{set_clause} "
        f"RETURN count(r) AS cnt"
    )
    with conn.session() as session:
        result = session.run(cypher, batch=edges)
        record = result.single()
        created = int(record["cnt"]) if record else 0

    if created < len(edges):
        logger.warning(
            "Edge %s (%s→%s): %d/%d created (some endpoints missing)",
            edge_label,
            from_label,
            to_label,
            created,
            len(edges),
        )
    else:
        logger.info(
            "Edge %s (%s→%s): %d created",
            edge_label,
            from_label,
            to_label,
            created,
        )
    return created


def _delete_legacy_flow_triggers(conn: Neo4jConnection) -> int:
    """Delete any leftover v2 ``FLOW_TRIGGERS`` edges. No-op if already clean."""
    with conn.session() as session:
        result = session.run(
            "MATCH ()-[r:FLOW_TRIGGERS]->() WITH r, count(*) AS _ DELETE r RETURN count(_) AS cnt"
        )
        record = result.single()
        return int(record["cnt"]) if record else 0


# ---------------------------------------------------------------------------
# Importer driver
# ---------------------------------------------------------------------------


@dataclass
class ImportReport:
    """Summary of a single ``run_import`` invocation."""

    flows_upserted: int = 0
    flows_deleted: int = 0
    messages_upserted: int = 0
    messages_deleted: int = 0
    events_upserted: int = 0
    events_deleted: int = 0
    http_clients_upserted: int = 0
    http_clients_deleted: int = 0

    flow_entry_edges: int = 0
    flow_entry_class_edges: int = 0
    emits_flow_message_edges: int = 0
    emits_flow_event_edges: int = 0
    emits_call_message_edges: int = 0
    emits_call_event_edges: int = 0
    uses_http_flow_edges: int = 0
    uses_http_call_edges: int = 0
    handled_by_message_edges: int = 0
    handled_by_event_edges: int = 0
    of_type_message_edges: int = 0
    of_type_event_edges: int = 0
    of_type_http_edges: int = 0

    qdrant_points_deleted: int = 0
    legacy_flow_triggers_deleted: int = 0

    deleted_flow_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def run_import(
    conn: Neo4jConnection,
    path: str | Path,
    qdrant_url: str | None = None,
    qdrant_api_key: str | None = None,
) -> ImportReport:
    """Import the v3 ``symfony-kloc.json`` at ``path`` into Neo4j.

    When ``qdrant_url`` is provided, orphan :Flow points in the
    ``flow_explain_embeddings`` Qdrant collection are filter-deleted by
    ``flow_id``. Without a URL the import remains a Neo4j-only operation and
    ``qdrant_points_deleted`` stays 0.
    """
    data = load_symfony_kloc(path)
    flows, messages, events, http_clients, edges = parse_v3(data)

    legacy_deleted = _delete_legacy_flow_triggers(conn)

    flows_up, deleted_flow_ids = _reconcile_nodes(
        conn,
        "Flow",
        "flow_id",
        flows,
        preserve_props=FLOW_PRESERVED_PROPS,
    )
    messages_up, deleted_message_ids = _reconcile_nodes(conn, "Message", "id", messages)
    events_up, deleted_event_ids = _reconcile_nodes(conn, "Event", "id", events)
    http_up, deleted_http_ids = _reconcile_nodes(conn, "HttpClient", "id", http_clients)

    report = ImportReport(
        flows_upserted=flows_up,
        flows_deleted=len(deleted_flow_ids),
        messages_upserted=messages_up,
        messages_deleted=len(deleted_message_ids),
        events_upserted=events_up,
        events_deleted=len(deleted_event_ids),
        http_clients_upserted=http_up,
        http_clients_deleted=len(deleted_http_ids),
        legacy_flow_triggers_deleted=legacy_deleted,
        deleted_flow_ids=list(deleted_flow_ids),
    )

    report.flow_entry_edges = _bulk_replace_edges(
        conn,
        edge_label="FLOW_ENTRY",
        from_label="Flow",
        from_id_key="flow_id",
        to_label="Node",
        to_id_key="node_id",
        edges=edges["flow_entry"],
        from_field="flow_id",
        to_field="method_node_id",
    )
    report.flow_entry_class_edges = _bulk_replace_edges(
        conn,
        edge_label="FLOW_ENTRY_CLASS",
        from_label="Flow",
        from_id_key="flow_id",
        to_label="Node",
        to_id_key="node_id",
        edges=edges["flow_entry_class"],
        from_field="flow_id",
        to_field="class_node_id",
    )

    emits_msg_flow_props = ["call_node_id", "caller_method_fqn", "caller_method_node_id"]
    report.emits_flow_message_edges = _bulk_replace_edges(
        conn,
        edge_label="EMITS",
        from_label="Flow",
        from_id_key="flow_id",
        to_label="Message",
        to_id_key="id",
        edges=edges["emits_message_flow"],
        from_field="flow_id",
        to_field="message_id",
        prop_fields=emits_msg_flow_props,
    )
    report.emits_flow_event_edges = _bulk_replace_edges(
        conn,
        edge_label="EMITS",
        from_label="Flow",
        from_id_key="flow_id",
        to_label="Event",
        to_id_key="id",
        edges=edges["emits_event_flow"],
        from_field="flow_id",
        to_field="event_id",
        prop_fields=emits_msg_flow_props,
    )
    report.emits_call_message_edges = _bulk_replace_edges(
        conn,
        edge_label="EMITS",
        from_label="Call",
        from_id_key="node_id",
        to_label="Message",
        to_id_key="id",
        edges=edges["emits_message_call"],
        from_field="call_node_id",
        to_field="message_id",
    )
    report.emits_call_event_edges = _bulk_replace_edges(
        conn,
        edge_label="EMITS",
        from_label="Call",
        from_id_key="node_id",
        to_label="Event",
        to_id_key="id",
        edges=edges["emits_event_call"],
        from_field="call_node_id",
        to_field="event_id",
    )

    uses_http_props = ["call_node_id", "caller_method_fqn", "caller_method_node_id"]
    report.uses_http_flow_edges = _bulk_replace_edges(
        conn,
        edge_label="USES_HTTP_CLIENT",
        from_label="Flow",
        from_id_key="flow_id",
        to_label="HttpClient",
        to_id_key="id",
        edges=edges["uses_http_flow"],
        from_field="flow_id",
        to_field="http_client_id",
        prop_fields=uses_http_props,
    )
    report.uses_http_call_edges = _bulk_replace_edges(
        conn,
        edge_label="USES_HTTP_CLIENT",
        from_label="Call",
        from_id_key="node_id",
        to_label="HttpClient",
        to_id_key="id",
        edges=edges["uses_http_call"],
        from_field="call_node_id",
        to_field="http_client_id",
    )

    report.handled_by_message_edges = _bulk_replace_edges(
        conn,
        edge_label="HANDLED_BY",
        from_label="Message",
        from_id_key="id",
        to_label="Flow",
        to_id_key="flow_id",
        edges=edges["handled_by_message"],
        from_field="message_id",
        to_field="flow_id",
    )
    report.handled_by_event_edges = _bulk_replace_edges(
        conn,
        edge_label="HANDLED_BY",
        from_label="Event",
        from_id_key="id",
        to_label="Flow",
        to_id_key="flow_id",
        edges=edges["handled_by_event"],
        from_field="event_id",
        to_field="flow_id",
        prop_fields=["priority"],
    )

    report.of_type_message_edges = _bulk_replace_edges(
        conn,
        edge_label="OF_TYPE",
        from_label="Message",
        from_id_key="id",
        to_label="Node",
        to_id_key="node_id",
        edges=edges["of_type_message"],
        from_field="message_id",
        to_field="class_node_id",
    )
    report.of_type_event_edges = _bulk_replace_edges(
        conn,
        edge_label="OF_TYPE",
        from_label="Event",
        from_id_key="id",
        to_label="Node",
        to_id_key="node_id",
        edges=edges["of_type_event"],
        from_field="event_id",
        to_field="class_node_id",
    )
    report.of_type_http_edges = _bulk_replace_edges(
        conn,
        edge_label="OF_TYPE",
        from_label="HttpClient",
        from_id_key="id",
        to_label="Node",
        to_id_key="node_id",
        edges=edges["of_type_http_client"],
        from_field="http_client_id",
        to_field="class_node_id",
    )

    if qdrant_url and deleted_flow_ids:
        from ..ai.flow_qdrant import delete_flow_embedding

        total = 0
        for fid in deleted_flow_ids:
            try:
                total += delete_flow_embedding(fid, qdrant_url, qdrant_api_key)
            except Exception as exc:
                logger.warning(
                    "Qdrant filter-delete failed for orphan flow %s: %s",
                    fid,
                    exc,
                )
        report.qdrant_points_deleted = total

    logger.info(
        "Import complete: flows=%d(+%d/-%d) messages=%d events=%d http_clients=%d "
        "qdrant_deleted=%d legacy_flow_triggers=%d",
        report.flows_upserted,
        report.flows_upserted,
        report.flows_deleted,
        report.messages_upserted,
        report.events_upserted,
        report.http_clients_upserted,
        report.qdrant_points_deleted,
        report.legacy_flow_triggers_deleted,
    )
    return report
