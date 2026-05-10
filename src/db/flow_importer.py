"""Import symfony-kloc.json flows into Neo4j as :Flow nodes with FLOW_ENTRY/FLOW_TRIGGERS edges.

Reads symfony-kloc.json (top-level ``flows[]`` + ``triggers[]``) and writes:
  - one :Flow node per app flow (MERGE on flow_id, idempotent within a single run)
  - one FLOW_ENTRY edge per flow with a resolvable method_node_id
  - one FLOW_TRIGGERS edge per (source, target) pair across all triggers[]

Idempotency across runs is provided by callers invoking clear_flows() before import.
"""

import json
import logging
from pathlib import Path

from .connection import Neo4jConnection

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
APP_NAMESPACE = "App\\"


def load_symfony_kloc(path: str | Path) -> dict:
    """Load and parse a symfony-kloc.json file."""
    with open(path) as f:
        return json.load(f)


def _is_app_flow(flow: dict) -> bool:
    """True if the flow's entry FQN is in the App namespace."""
    fqn = flow.get("entry", {}).get("fqn", "")
    return fqn.startswith(APP_NAMESPACE)


def _is_app_flow_id(flow_id: str) -> bool:
    """True if a flow_id string references the App namespace."""
    return APP_NAMESPACE in flow_id


def _derive_name(flow_type: str, entry: dict) -> str:
    """Derive a human-readable :Flow.name per spec D3a."""
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


def parse_flows(data: dict) -> tuple[list[dict], list[dict]]:
    """Parse symfony-kloc.json into (flow_nodes, flow_edges).

    flow_edges items have type='flow_entry' or 'flow_triggers'.
    """
    flows = data.get("flows", [])
    app_flows = [f for f in flows if _is_app_flow(f)]
    logger.info(
        "Parsing flows: %d total, %d app flows (filtered %d non-App)",
        len(flows),
        len(app_flows),
        len(flows) - len(app_flows),
    )

    nodes: list[dict] = []
    edges: list[dict] = []
    app_flow_ids: set[str] = set()

    for flow in app_flows:
        flow_id = flow["id"]
        flow_type = flow["type"]
        entry = flow.get("entry", {})

        node_props = {
            "flow_id": flow_id,
            "type": flow_type,
            "entry_fqn": entry.get("fqn", ""),
            "entry_method": entry.get("method", ""),
            "name": _derive_name(flow_type, entry),
        }

        if flow_type == "http":
            node_props["route"] = entry.get("route", "")
            node_props["http_methods"] = entry.get("http_methods", [])
        elif flow_type == "message":
            node_props["message_class"] = entry.get("message", "")
        elif flow_type == "event":
            node_props["event_name"] = entry.get("event", "")
        elif flow_type == "cli":
            node_props["command_name"] = entry.get("command_name", "")

        nodes.append(node_props)
        app_flow_ids.add(flow_id)
        logger.debug("  Flow node: %s (%s)", flow_id, flow_type)

        method_node_id = entry.get("method_node_id")
        if method_node_id:
            edges.append(
                {
                    "type": "flow_entry",
                    "source_flow_id": flow_id,
                    "target_node_id": method_node_id,
                }
            )

    for trigger in data.get("triggers", []):
        trigger_type = trigger.get("type", "")
        via = trigger.get("dispatched_class", "")
        for source in trigger.get("sources", []):
            source_id = source.get("flow_id", "")
            if not _is_app_flow_id(source_id):
                logger.warning("Skipping non-App trigger source: %s", source_id)
                continue
            for target in trigger.get("targets", []):
                target_id = target.get("flow_id", "")
                if not _is_app_flow_id(target_id):
                    logger.warning("Skipping non-App trigger target: %s", target_id)
                    continue
                edges.append(
                    {
                        "type": "flow_triggers",
                        "source_flow_id": source_id,
                        "target_flow_id": target_id,
                        "trigger_type": trigger_type,
                        "via": via,
                    }
                )

    return nodes, edges


def import_flow_nodes(connection: Neo4jConnection, nodes: list[dict]) -> int:
    """Import :Flow nodes via MERGE on flow_id (in-run idempotency)."""
    if not nodes:
        return 0

    query = "UNWIND $batch AS props MERGE (f:Flow {flow_id: props.flow_id}) SET f += props"
    total = 0
    for i in range(0, len(nodes), BATCH_SIZE):
        batch = nodes[i : i + BATCH_SIZE]
        with connection.session() as session:
            session.run(query, batch=batch)
        total += len(batch)
    logger.info("Imported %d :Flow nodes", total)
    return total


def import_flow_edges(connection: Neo4jConnection, edges: list[dict]) -> int:
    """Import FLOW_ENTRY and FLOW_TRIGGERS edges, skipping any with unresolved targets."""
    total = 0

    by_type: dict[str, list[dict]] = {}
    for edge in edges:
        by_type.setdefault(edge["type"], []).append(edge)

    for edge in by_type.get("flow_entry", []):
        flow_id = edge["source_flow_id"]
        node_id = edge["target_node_id"]
        with connection.session() as session:
            result = session.run(
                """
                MATCH (n:Node {node_id: $node_id})
                WITH n
                MATCH (f:Flow {flow_id: $flow_id})
                MERGE (f)-[r:FLOW_ENTRY]->(n)
                RETURN count(r) AS cnt
                """,
                flow_id=flow_id,
                node_id=node_id,
            )
            record = result.single()
            if record and record["cnt"] > 0:
                total += 1
            else:
                logger.warning(
                    "FLOW_ENTRY skipped: no :Node with node_id=%s for flow=%s",
                    node_id,
                    flow_id,
                )

    for edge in by_type.get("flow_triggers", []):
        source_id = edge["source_flow_id"]
        target_id = edge["target_flow_id"]
        with connection.session() as session:
            result = session.run(
                """
                MATCH (f1:Flow {flow_id: $source_id})
                MATCH (f2:Flow {flow_id: $target_id})
                MERGE (f1)-[r:FLOW_TRIGGERS]->(f2)
                SET r.trigger_type = $trigger_type, r.via = $via
                RETURN count(r) AS cnt
                """,
                source_id=source_id,
                target_id=target_id,
                trigger_type=edge.get("trigger_type", ""),
                via=edge.get("via", ""),
            )
            record = result.single()
            if record and record["cnt"] > 0:
                total += 1
            else:
                logger.warning(
                    "FLOW_TRIGGERS skipped: missing flow source=%s or target=%s",
                    source_id,
                    target_id,
                )

    logger.info("Imported %d Flow edges", total)
    return total


def clear_flows(connection: Neo4jConnection) -> None:
    """DETACH DELETE all :Flow nodes and their relationships."""
    with connection.session() as session:
        session.run("MATCH (f:Flow) DETACH DELETE f")
    logger.info("Cleared all :Flow nodes")
