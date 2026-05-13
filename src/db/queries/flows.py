"""Read-side Cypher queries for :Flow, :Message, :Event, :HttpClient nodes.

Pairs with src/db/flow_importer.py (write side). All functions take a
Neo4jConnection and return plain dicts — no model objects, no I/O.

v3.0 (symfony-v3): replaces v2 ``triggers_in``/``triggers_out`` projection on
:Flow detail with ``dispatches_in``/``dispatches_out`` keyed by entity kind
(messages, events, http_clients). Adds read-side queries for the three new
node labels (:Message, :Event, :HttpClient).
"""

import logging

from ..connection import Neo4jConnection

logger = logging.getLogger(__name__)


VALID_FLOW_TYPES = {"http", "message", "event", "cli"}


def _flow_node_to_dict(node) -> dict:
    """Project a :Flow node record into the shared list/candidate dict shape."""
    record: dict = {
        "flow_id": node["flow_id"],
        "type": node.get("type", ""),
        "name": node.get("name", ""),
        "entry_fqn": node.get("entry_fqn", ""),
    }
    if record["type"] == "http":
        record["route"] = node.get("route", "")
        record["http_methods"] = list(node.get("http_methods") or [])
    elif record["type"] == "message":
        record["message_class"] = node.get("message_class", "")
    elif record["type"] == "event":
        record["event_name"] = node.get("event_name", "")
    elif record["type"] == "cli":
        record["command_name"] = node.get("command_name", "")
    return record


def list_flows(
    connection: Neo4jConnection,
    type_filter: list[str] | None = None,
) -> list[dict]:
    """Return all :Flow nodes, optionally filtered by type.

    type_filter values must be in VALID_FLOW_TYPES; unknown values are silently ignored.
    Results are sorted by (type, flow_id) for stable output.
    """
    types = None
    if type_filter:
        types = [t for t in type_filter if t in VALID_FLOW_TYPES]
        if not types:
            return []

    query = (
        "MATCH (f:Flow) "
        + ("WHERE f.type IN $types " if types else "")
        + "OPTIONAL MATCH (f)-[:FLOW_ENTRY]->(m:Node) "
        + "RETURN f, m.node_id AS method_node_id "
        + "ORDER BY f.type, f.flow_id"
    )

    with connection.session() as session:
        records = list(session.run(query, types=types))

    out: list[dict] = []
    for record in records:
        flow = _flow_node_to_dict(record["f"])
        flow["entry_method_node_id"] = record["method_node_id"] or ""
        out.append(flow)
    return out


def find_flow(connection: Neo4jConnection, query: str) -> list[dict]:
    """Find :Flow candidates matching `query` against flow_id, entry_fqn, or name.

    Match cascade:
      1. Exact flow_id
      2. Substring on flow_id, entry_fqn, or name (case-insensitive)
    Returns 0..N candidates (caller decides list/detail/candidate UX).
    """
    normalized = (query or "").strip()
    if not normalized:
        return []

    cypher = (
        "MATCH (f:Flow) "
        "WHERE f.flow_id = $q "
        "OPTIONAL MATCH (f)-[:FLOW_ENTRY]->(m:Node) "
        "RETURN f, m.node_id AS method_node_id "
        "ORDER BY f.type, f.flow_id"
    )
    with connection.session() as session:
        exact = list(session.run(cypher, q=normalized))
    if exact:
        return [
            {
                **_flow_node_to_dict(r["f"]),
                "entry_method_node_id": r["method_node_id"] or "",
            }
            for r in exact
        ]

    cypher = (
        "MATCH (f:Flow) "
        "WHERE toLower(f.flow_id) CONTAINS toLower($q) "
        "   OR toLower(f.entry_fqn) CONTAINS toLower($q) "
        "   OR toLower(f.name) CONTAINS toLower($q) "
        "OPTIONAL MATCH (f)-[:FLOW_ENTRY]->(m:Node) "
        "RETURN f, m.node_id AS method_node_id "
        "ORDER BY f.type, f.flow_id "
        "LIMIT 50"
    )
    with connection.session() as session:
        rows = list(session.run(cypher, q=normalized))
    return [
        {
            **_flow_node_to_dict(r["f"]),
            "entry_method_node_id": r["method_node_id"] or "",
        }
        for r in rows
    ]


def get_flow_detail(connection: Neo4jConnection, flow_id: str) -> dict | None:
    """Return full detail for a single :Flow including entry node + dispatch context.

    The detail dict has:
      - top-level :Flow properties (flow_id, type, name, entry_fqn, …)
      - ``entry``  — entry method/class info (fqn, file, line range, method_node_id)
      - ``dispatches_out`` — what this flow emits (messages/events/http_clients)
      - ``dispatches_in``  — what messages/events flow into this flow (as a handler)
      - optional ``explanation`` + ``explain_model`` when set by ``enrich-flows``

    Returns None if no flow exists with this exact flow_id.
    """
    head_query = (
        "MATCH (f:Flow {flow_id: $flow_id}) OPTIONAL MATCH (f)-[:FLOW_ENTRY]->(m:Node) RETURN f, m"
    )
    with connection.session() as session:
        head = session.run(head_query, flow_id=flow_id).single()
    if head is None:
        return None
    flow_node = head["f"]
    method = head["m"]

    flow = _flow_node_to_dict(flow_node)
    class_fqn = flow_node.get("entry_fqn", "")
    method_name = flow_node.get("entry_method", "")
    full_fqn = f"{class_fqn}::{method_name}" if class_fqn and method_name else class_fqn
    explanation = flow_node.get("explanation")
    explain_model = flow_node.get("explain_model")
    if explanation:
        flow["explanation"] = explanation
        if explain_model:
            flow["explain_model"] = explain_model
    entry: dict = {
        "fqn": full_fqn,
        "class_fqn": class_fqn,
        "method": method_name,
    }
    if method is not None:
        entry["method_node_id"] = method.get("node_id", "")
        entry["file"] = method.get("file") or ""
        start = method.get("start_line")
        end = method.get("end_line")
        entry["start_line"] = (start + 1) if start is not None else None
        entry["end_line"] = (end + 1) if end is not None else None
    else:
        entry["method_node_id"] = ""
        entry["file"] = ""
        entry["start_line"] = None
        entry["end_line"] = None

    # ---- dispatches_out ----
    # Messages emitted by this flow (one row per Flow→Message EMITS edge)
    out_msg_query = (
        "MATCH (f:Flow {flow_id: $flow_id})-[em:EMITS]->(m:Message) "
        "OPTIONAL MATCH (m)-[:HANDLED_BY]->(t:Flow) "
        "RETURN m.id AS id, m.fqn AS fqn, m.transports AS transports, "
        "       em.caller_method_fqn AS caller_method_fqn, "
        "       em.call_node_id AS call_node_id, "
        "       collect(DISTINCT t.flow_id) AS target_flow_ids "
        "ORDER BY m.id, em.call_node_id"
    )
    # Events emitted by this flow
    out_evt_query = (
        "MATCH (f:Flow {flow_id: $flow_id})-[em:EMITS]->(e:Event) "
        "OPTIONAL MATCH (e)-[r:HANDLED_BY]->(t:Flow) "
        "RETURN e.id AS id, e.fqn AS fqn, "
        "       em.caller_method_fqn AS caller_method_fqn, "
        "       em.call_node_id AS call_node_id, "
        "       collect(DISTINCT {flow_id: t.flow_id, priority: r.priority}) AS targets "
        "ORDER BY e.id, em.call_node_id"
    )
    # HTTP clients used by this flow
    out_http_query = (
        "MATCH (f:Flow {flow_id: $flow_id})-[u:USES_HTTP_CLIENT]->(h:HttpClient) "
        "RETURN h.id AS id, h.service_id AS service_id, h.base_uri AS base_uri, "
        "       h.class_fqn AS class_fqn, "
        "       u.caller_method_fqn AS caller_method_fqn, "
        "       u.call_node_id AS call_node_id "
        "ORDER BY h.id, u.call_node_id"
    )

    # ---- dispatches_in ----
    # Messages whose HANDLED_BY targets this flow
    in_msg_query = (
        "MATCH (m:Message)-[:HANDLED_BY]->(f:Flow {flow_id: $flow_id}) "
        "OPTIONAL MATCH (s:Flow)-[:EMITS]->(m) "
        "RETURN m.id AS id, m.fqn AS fqn, "
        "       collect(DISTINCT s.flow_id) AS source_flow_ids "
        "ORDER BY m.id"
    )
    # Events whose HANDLED_BY targets this flow
    in_evt_query = (
        "MATCH (e:Event)-[r:HANDLED_BY]->(f:Flow {flow_id: $flow_id}) "
        "OPTIONAL MATCH (s:Flow)-[:EMITS]->(e) "
        "RETURN e.id AS id, e.fqn AS fqn, r.priority AS priority, "
        "       collect(DISTINCT s.flow_id) AS source_flow_ids "
        "ORDER BY e.id"
    )

    with connection.session() as session:
        out_msgs = list(session.run(out_msg_query, flow_id=flow_id))
        out_evts = list(session.run(out_evt_query, flow_id=flow_id))
        out_https = list(session.run(out_http_query, flow_id=flow_id))
        in_msgs = list(session.run(in_msg_query, flow_id=flow_id))
        in_evts = list(session.run(in_evt_query, flow_id=flow_id))

    dispatches_out_messages = [
        {
            "id": r["id"],
            "fqn": r["fqn"] or "",
            "transports": list(r["transports"] or []),
            "caller_method_fqn": r["caller_method_fqn"] or "",
            "call_node_id": r["call_node_id"] or "",
            "target_flow_ids": [t for t in (r["target_flow_ids"] or []) if t],
        }
        for r in out_msgs
    ]
    dispatches_out_events = [
        {
            "id": r["id"],
            "fqn": r["fqn"] or "",
            "caller_method_fqn": r["caller_method_fqn"] or "",
            "call_node_id": r["call_node_id"] or "",
            "targets": [
                {"flow_id": t["flow_id"], "priority": t.get("priority", 0) or 0}
                for t in (r["targets"] or [])
                if t and t.get("flow_id")
            ],
        }
        for r in out_evts
    ]
    dispatches_out_http = [
        {
            "id": r["id"],
            "service_id": r["service_id"] or "",
            "base_uri": r["base_uri"] or "",
            "class_fqn": r["class_fqn"] or "",
            "caller_method_fqn": r["caller_method_fqn"] or "",
            "call_node_id": r["call_node_id"] or "",
        }
        for r in out_https
    ]
    dispatches_in_messages = [
        {
            "id": r["id"],
            "fqn": r["fqn"] or "",
            "source_flow_ids": [s for s in (r["source_flow_ids"] or []) if s],
        }
        for r in in_msgs
    ]
    dispatches_in_events = [
        {
            "id": r["id"],
            "fqn": r["fqn"] or "",
            "priority": r["priority"] if r["priority"] is not None else 0,
            "source_flow_ids": [s for s in (r["source_flow_ids"] or []) if s],
        }
        for r in in_evts
    ]

    detail: dict = {
        **flow,
        "entry": entry,
        "dispatches_out": {
            "messages": dispatches_out_messages,
            "events": dispatches_out_events,
            "http_clients": dispatches_out_http,
        },
        "dispatches_in": {
            "messages": dispatches_in_messages,
            "events": dispatches_in_events,
        },
    }
    return detail


# ---------------------------------------------------------------------------
# :Message read-side queries
# ---------------------------------------------------------------------------


def list_messages(connection: Neo4jConnection) -> list[dict]:
    """Return all :Message nodes with source-flow and target-flow counts.

    Row shape: {id, fqn, transports, sources_count, targets_count}.
    Sorted by id for stable output.
    """
    query = (
        "MATCH (m:Message) "
        "OPTIONAL MATCH (sf:Flow)-[:EMITS]->(m) "
        "WITH m, count(DISTINCT sf) AS sources_count "
        "OPTIONAL MATCH (m)-[:HANDLED_BY]->(tf:Flow) "
        "RETURN m.id AS id, m.fqn AS fqn, m.transports AS transports, "
        "       sources_count, count(DISTINCT tf) AS targets_count "
        "ORDER BY m.id"
    )
    with connection.session() as session:
        records = list(session.run(query))
    return [
        {
            "id": r["id"],
            "fqn": r["fqn"] or "",
            "transports": list(r["transports"] or []),
            "sources_count": r["sources_count"],
            "targets_count": r["targets_count"],
        }
        for r in records
    ]


def find_message(connection: Neo4jConnection, query: str) -> list[dict]:
    """Find :Message candidates matching `query` against id or fqn.

    Match cascade:
      1. Exact id
      2. Substring on id or fqn (case-insensitive)
    """
    normalized = (query or "").strip()
    if not normalized:
        return []

    exact_cypher = (
        "MATCH (m:Message {id: $q}) "
        "OPTIONAL MATCH (sf:Flow)-[:EMITS]->(m) "
        "WITH m, count(DISTINCT sf) AS sources_count "
        "OPTIONAL MATCH (m)-[:HANDLED_BY]->(tf:Flow) "
        "RETURN m.id AS id, m.fqn AS fqn, m.transports AS transports, "
        "       sources_count, count(DISTINCT tf) AS targets_count "
        "ORDER BY m.id"
    )
    with connection.session() as session:
        exact = list(session.run(exact_cypher, q=normalized))
    if exact:
        return [_message_row(r) for r in exact]

    partial_cypher = (
        "MATCH (m:Message) "
        "WHERE toLower(m.id) CONTAINS toLower($q) "
        "   OR toLower(m.fqn) CONTAINS toLower($q) "
        "OPTIONAL MATCH (sf:Flow)-[:EMITS]->(m) "
        "WITH m, count(DISTINCT sf) AS sources_count "
        "OPTIONAL MATCH (m)-[:HANDLED_BY]->(tf:Flow) "
        "RETURN m.id AS id, m.fqn AS fqn, m.transports AS transports, "
        "       sources_count, count(DISTINCT tf) AS targets_count "
        "ORDER BY m.id LIMIT 50"
    )
    with connection.session() as session:
        rows = list(session.run(partial_cypher, q=normalized))
    return [_message_row(r) for r in rows]


def _message_row(record) -> dict:
    return {
        "id": record["id"],
        "fqn": record["fqn"] or "",
        "transports": list(record["transports"] or []),
        "sources_count": record["sources_count"],
        "targets_count": record["targets_count"],
    }


def get_message_detail(connection: Neo4jConnection, message_id: str) -> dict | None:
    """Return full detail for a single :Message.

    Detail dict:
      - id, fqn, transports
      - sources: list of {flow_id, caller_method_fqn, call_node_id, caller_method_node_id}
      - targets: list of {flow_id}    (one per HANDLED_BY edge)
      - of_type_class_fqn: FQN of the OF_TYPE class node, or None
    """
    head_query = "MATCH (m:Message {id: $mid}) RETURN m"
    with connection.session() as session:
        head = session.run(head_query, mid=message_id).single()
    if head is None:
        return None
    node = head["m"]

    sources_query = (
        "MATCH (f:Flow)-[em:EMITS]->(m:Message {id: $mid}) "
        "RETURN f.flow_id AS flow_id, em.caller_method_fqn AS caller_method_fqn, "
        "       em.call_node_id AS call_node_id, "
        "       em.caller_method_node_id AS caller_method_node_id "
        "ORDER BY f.flow_id, em.call_node_id"
    )
    targets_query = (
        "MATCH (m:Message {id: $mid})-[:HANDLED_BY]->(t:Flow) "
        "RETURN t.flow_id AS flow_id ORDER BY t.flow_id"
    )
    of_type_query = "MATCH (m:Message {id: $mid})-[:OF_TYPE]->(c:Node) RETURN c.fqn AS fqn LIMIT 1"

    with connection.session() as session:
        sources = list(session.run(sources_query, mid=message_id))
        targets = list(session.run(targets_query, mid=message_id))
        of_type = session.run(of_type_query, mid=message_id).single()

    return {
        "id": node["id"],
        "fqn": node.get("fqn", ""),
        "transports": list(node.get("transports") or []),
        "sources": [
            {
                "flow_id": r["flow_id"],
                "caller_method_fqn": r["caller_method_fqn"] or "",
                "call_node_id": r["call_node_id"] or "",
                "caller_method_node_id": r["caller_method_node_id"] or "",
            }
            for r in sources
        ],
        "targets": [{"flow_id": r["flow_id"]} for r in targets],
        "of_type_class_fqn": of_type["fqn"] if of_type else None,
    }


# ---------------------------------------------------------------------------
# :Event read-side queries
# ---------------------------------------------------------------------------


def list_events(connection: Neo4jConnection) -> list[dict]:
    """Return all :Event nodes with source-flow and target-flow counts.

    Row shape: {id, fqn, sources_count, targets_count}.
    """
    query = (
        "MATCH (e:Event) "
        "OPTIONAL MATCH (sf:Flow)-[:EMITS]->(e) "
        "WITH e, count(DISTINCT sf) AS sources_count "
        "OPTIONAL MATCH (e)-[:HANDLED_BY]->(tf:Flow) "
        "RETURN e.id AS id, e.fqn AS fqn, "
        "       sources_count, count(DISTINCT tf) AS targets_count "
        "ORDER BY e.id"
    )
    with connection.session() as session:
        records = list(session.run(query))
    return [_event_row(r) for r in records]


def find_event(connection: Neo4jConnection, query: str) -> list[dict]:
    """Find :Event candidates matching `query` against id or fqn."""
    normalized = (query or "").strip()
    if not normalized:
        return []

    exact_cypher = (
        "MATCH (e:Event {id: $q}) "
        "OPTIONAL MATCH (sf:Flow)-[:EMITS]->(e) "
        "WITH e, count(DISTINCT sf) AS sources_count "
        "OPTIONAL MATCH (e)-[:HANDLED_BY]->(tf:Flow) "
        "RETURN e.id AS id, e.fqn AS fqn, "
        "       sources_count, count(DISTINCT tf) AS targets_count "
        "ORDER BY e.id"
    )
    with connection.session() as session:
        exact = list(session.run(exact_cypher, q=normalized))
    if exact:
        return [_event_row(r) for r in exact]

    partial_cypher = (
        "MATCH (e:Event) "
        "WHERE toLower(e.id) CONTAINS toLower($q) "
        "   OR toLower(e.fqn) CONTAINS toLower($q) "
        "OPTIONAL MATCH (sf:Flow)-[:EMITS]->(e) "
        "WITH e, count(DISTINCT sf) AS sources_count "
        "OPTIONAL MATCH (e)-[:HANDLED_BY]->(tf:Flow) "
        "RETURN e.id AS id, e.fqn AS fqn, "
        "       sources_count, count(DISTINCT tf) AS targets_count "
        "ORDER BY e.id LIMIT 50"
    )
    with connection.session() as session:
        rows = list(session.run(partial_cypher, q=normalized))
    return [_event_row(r) for r in rows]


def _event_row(record) -> dict:
    return {
        "id": record["id"],
        "fqn": record["fqn"] or "",
        "sources_count": record["sources_count"],
        "targets_count": record["targets_count"],
    }


def get_event_detail(connection: Neo4jConnection, event_id: str) -> dict | None:
    """Return full detail for a single :Event.

    Detail dict:
      - id, fqn
      - sources: list of {flow_id, caller_method_fqn, call_node_id, caller_method_node_id}
      - targets: list of {flow_id, priority}  (one per HANDLED_BY edge)
      - of_type_class_fqn: FQN of the OF_TYPE class node, or None
    """
    head_query = "MATCH (e:Event {id: $eid}) RETURN e"
    with connection.session() as session:
        head = session.run(head_query, eid=event_id).single()
    if head is None:
        return None
    node = head["e"]

    sources_query = (
        "MATCH (f:Flow)-[em:EMITS]->(e:Event {id: $eid}) "
        "RETURN f.flow_id AS flow_id, em.caller_method_fqn AS caller_method_fqn, "
        "       em.call_node_id AS call_node_id, "
        "       em.caller_method_node_id AS caller_method_node_id "
        "ORDER BY f.flow_id, em.call_node_id"
    )
    targets_query = (
        "MATCH (e:Event {id: $eid})-[r:HANDLED_BY]->(t:Flow) "
        "RETURN t.flow_id AS flow_id, r.priority AS priority ORDER BY t.flow_id"
    )
    of_type_query = "MATCH (e:Event {id: $eid})-[:OF_TYPE]->(c:Node) RETURN c.fqn AS fqn LIMIT 1"

    with connection.session() as session:
        sources = list(session.run(sources_query, eid=event_id))
        targets = list(session.run(targets_query, eid=event_id))
        of_type = session.run(of_type_query, eid=event_id).single()

    return {
        "id": node["id"],
        "fqn": node.get("fqn", ""),
        "sources": [
            {
                "flow_id": r["flow_id"],
                "caller_method_fqn": r["caller_method_fqn"] or "",
                "call_node_id": r["call_node_id"] or "",
                "caller_method_node_id": r["caller_method_node_id"] or "",
            }
            for r in sources
        ],
        "targets": [
            {
                "flow_id": r["flow_id"],
                "priority": r["priority"] if r["priority"] is not None else 0,
            }
            for r in targets
        ],
        "of_type_class_fqn": of_type["fqn"] if of_type else None,
    }


# ---------------------------------------------------------------------------
# :HttpClient read-side queries
# ---------------------------------------------------------------------------


def list_http_clients(connection: Neo4jConnection) -> list[dict]:
    """Return all :HttpClient nodes with source-flow counts.

    Row shape: {id, service_id, base_uri, class_fqn, sources_count}.
    """
    query = (
        "MATCH (h:HttpClient) "
        "OPTIONAL MATCH (sf:Flow)-[:USES_HTTP_CLIENT]->(h) "
        "RETURN h.id AS id, h.service_id AS service_id, h.base_uri AS base_uri, "
        "       h.class_fqn AS class_fqn, count(DISTINCT sf) AS sources_count "
        "ORDER BY h.id"
    )
    with connection.session() as session:
        records = list(session.run(query))
    return [_http_client_row(r) for r in records]


def find_http_client(connection: Neo4jConnection, query: str) -> list[dict]:
    """Find :HttpClient candidates matching `query` against id, service_id, or class_fqn."""
    normalized = (query or "").strip()
    if not normalized:
        return []

    exact_cypher = (
        "MATCH (h:HttpClient) "
        "WHERE h.id = $q OR h.service_id = $q "
        "OPTIONAL MATCH (sf:Flow)-[:USES_HTTP_CLIENT]->(h) "
        "RETURN h.id AS id, h.service_id AS service_id, h.base_uri AS base_uri, "
        "       h.class_fqn AS class_fqn, count(DISTINCT sf) AS sources_count "
        "ORDER BY h.id"
    )
    with connection.session() as session:
        exact = list(session.run(exact_cypher, q=normalized))
    if exact:
        return [_http_client_row(r) for r in exact]

    partial_cypher = (
        "MATCH (h:HttpClient) "
        "WHERE toLower(h.id) CONTAINS toLower($q) "
        "   OR toLower(h.service_id) CONTAINS toLower($q) "
        "   OR toLower(h.class_fqn) CONTAINS toLower($q) "
        "OPTIONAL MATCH (sf:Flow)-[:USES_HTTP_CLIENT]->(h) "
        "RETURN h.id AS id, h.service_id AS service_id, h.base_uri AS base_uri, "
        "       h.class_fqn AS class_fqn, count(DISTINCT sf) AS sources_count "
        "ORDER BY h.id LIMIT 50"
    )
    with connection.session() as session:
        rows = list(session.run(partial_cypher, q=normalized))
    return [_http_client_row(r) for r in rows]


def _http_client_row(record) -> dict:
    return {
        "id": record["id"],
        "service_id": record["service_id"] or "",
        "base_uri": record["base_uri"] or "",
        "class_fqn": record["class_fqn"] or "",
        "sources_count": record["sources_count"],
    }


def get_http_client_detail(connection: Neo4jConnection, http_client_id: str) -> dict | None:
    """Return full detail for a single :HttpClient.

    Detail dict:
      - id, service_id, base_uri, class_fqn
      - sources: list of {flow_id, caller_method_fqn, call_node_id, caller_method_node_id}
      - of_type_class_fqn: FQN of the OF_TYPE class node, or None (vendor classes)
    """
    head_query = "MATCH (h:HttpClient {id: $hid}) RETURN h"
    with connection.session() as session:
        head = session.run(head_query, hid=http_client_id).single()
    if head is None:
        return None
    node = head["h"]

    sources_query = (
        "MATCH (f:Flow)-[u:USES_HTTP_CLIENT]->(h:HttpClient {id: $hid}) "
        "RETURN f.flow_id AS flow_id, u.caller_method_fqn AS caller_method_fqn, "
        "       u.call_node_id AS call_node_id, "
        "       u.caller_method_node_id AS caller_method_node_id "
        "ORDER BY f.flow_id, u.call_node_id"
    )
    of_type_query = (
        "MATCH (h:HttpClient {id: $hid})-[:OF_TYPE]->(c:Node) RETURN c.fqn AS fqn LIMIT 1"
    )

    with connection.session() as session:
        sources = list(session.run(sources_query, hid=http_client_id))
        of_type = session.run(of_type_query, hid=http_client_id).single()

    return {
        "id": node["id"],
        "service_id": node.get("service_id", ""),
        "base_uri": node.get("base_uri", ""),
        "class_fqn": node.get("class_fqn", ""),
        "sources": [
            {
                "flow_id": r["flow_id"],
                "caller_method_fqn": r["caller_method_fqn"] or "",
                "call_node_id": r["call_node_id"] or "",
                "caller_method_node_id": r["caller_method_node_id"] or "",
            }
            for r in sources
        ],
        "of_type_class_fqn": of_type["fqn"] if of_type else None,
    }
