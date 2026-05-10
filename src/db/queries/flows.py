"""Read-side Cypher queries for :Flow nodes (list, find, detail).

Pairs with src/db/flow_importer.py (write side). All functions take a
Neo4jConnection and return plain dicts — no model objects, no I/O.
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
    """Return full detail for a single :Flow including entry node + triggers in/out.

    Returns None if no flow exists with this exact flow_id.
    """
    head_query = (
        "MATCH (f:Flow {flow_id: $flow_id}) "
        "OPTIONAL MATCH (f)-[:FLOW_ENTRY]->(m:Node) "
        "RETURN f, m"
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

    out_query = (
        "MATCH (f:Flow {flow_id: $flow_id})-[r:FLOW_TRIGGERS]->(t:Flow) "
        "RETURN r.trigger_type AS trigger_type, r.via AS via, "
        "       t.flow_id AS target_flow_id, t.name AS target_name, t.type AS target_type "
        "ORDER BY t.flow_id"
    )
    in_query = (
        "MATCH (s:Flow)-[r:FLOW_TRIGGERS]->(f:Flow {flow_id: $flow_id}) "
        "RETURN r.trigger_type AS trigger_type, r.via AS via, "
        "       s.flow_id AS source_flow_id, s.name AS source_name, s.type AS source_type "
        "ORDER BY s.flow_id"
    )
    with connection.session() as session:
        out_edges = list(session.run(out_query, flow_id=flow_id))
        in_edges = list(session.run(in_query, flow_id=flow_id))

    triggers_out = [
        {
            "trigger_type": r["trigger_type"] or "",
            "via": r["via"] or "",
            "target_flow_id": r["target_flow_id"],
            "target_name": r["target_name"] or "",
            "target_type": r["target_type"] or "",
        }
        for r in out_edges
    ]
    triggers_in = [
        {
            "trigger_type": r["trigger_type"] or "",
            "via": r["via"] or "",
            "source_flow_id": r["source_flow_id"],
            "source_name": r["source_name"] or "",
            "source_type": r["source_type"] or "",
        }
        for r in in_edges
    ]

    detail: dict = {
        **flow,
        "entry": entry,
        "triggers_out": triggers_out,
        "triggers_in": triggers_in,
    }
    return detail
