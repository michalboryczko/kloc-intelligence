"""Import symfony-kloc.json flows into Neo4j as Flow nodes with edges to existing code nodes."""

import json
import logging
from pathlib import Path

from .connection import Neo4jConnection

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


def load_symfony_kloc(path: str | Path) -> dict:
    """Load and parse a symfony-kloc.json file."""
    with open(path, "r") as f:
        return json.load(f)


def _is_app_flow(flow: dict) -> bool:
    """Filter: only import application flows, skip Symfony framework internals."""
    fqn = flow.get("entry", {}).get("fqn", "")
    return fqn.startswith("App\\")


def parse_flows(data: dict) -> tuple[list[dict], list[dict]]:
    """Parse symfony-kloc.json into (flow_nodes, flow_edges).

    Returns:
        flow_nodes: list of dicts with Flow node properties
        flow_edges: list of dicts with edge properties (type, source, target, etc.)
    """
    flows = data.get("flows", [])
    app_flows = [f for f in flows if _is_app_flow(f)]

    logger.info("Parsing flows: %d total, %d app flows (filtered %d framework)",
                len(flows), len(app_flows), len(flows) - len(app_flows))

    nodes = []
    edges = []

    for flow in app_flows:
        flow_id = flow["id"]
        flow_type = flow["type"]
        entry = flow.get("entry", {})

        # Build Flow node properties
        node_props = {
            "flow_id": flow_id,
            "type": flow_type,
            "entry_fqn": entry.get("fqn", ""),
            "entry_method": entry.get("method", ""),
        }

        # Type-specific properties
        if flow_type == "http":
            node_props["route"] = entry.get("route", "")
            node_props["http_methods"] = entry.get("http_methods", [])
            node_props["name"] = f"{','.join(entry.get('http_methods', []))} {entry.get('route', '')}"
        elif flow_type == "message":
            node_props["message_class"] = entry.get("message", "")
            node_props["name"] = entry.get("message", "").rsplit("\\", 1)[-1]
        elif flow_type == "event":
            node_props["event_name"] = entry.get("event", "")
            node_props["name"] = entry.get("event", "").rsplit("\\", 1)[-1]
        elif flow_type == "cli":
            node_props["command_name"] = entry.get("command_name", "")
            node_props["name"] = entry.get("command_name", "")

        nodes.append(node_props)
        logger.debug("  Flow node: %s (%s)", flow_id, flow_type)

        # FLOW_ENTRY edge: Flow -> entry method
        entry_method_node_id = entry.get("method_node_id")
        if entry_method_node_id:
            edges.append({
                "type": "flow_entry",
                "source_flow_id": flow_id,
                "target_node_id": entry_method_node_id,
            })
            logger.debug("    FLOW_ENTRY -> %s", entry.get("fqn", ""))

        # FLOW_STEP edges: Flow -> chain step classes
        chain = flow.get("chain", [])
        for position, step in enumerate(chain):
            step_node_id = step.get("node_id")
            if step_node_id:
                edges.append({
                    "type": "flow_step",
                    "source_flow_id": flow_id,
                    "target_node_id": step_node_id,
                    "position": position,
                    "role": step.get("role", ""),
                })
                logger.debug("    FLOW_STEP[%d] -> %s (%s)", position, step.get("fqn", ""), step.get("role", ""))

            # Check for triggers on this step
            for trigger in step.get("triggers", []):
                trigger_type = trigger.get("type", "")
                via = trigger.get("via", "")
                for target_flow_id in trigger.get("target_flows", []):
                    # Only link to app flows
                    if "App\\" in target_flow_id:
                        edges.append({
                            "type": "flow_triggers",
                            "source_flow_id": flow_id,
                            "target_flow_id": target_flow_id,
                            "trigger_type": trigger_type,
                            "via": via,
                        })
                        logger.debug("    TRIGGERS -> %s (via %s)", target_flow_id, via)

    return nodes, edges


def import_flow_nodes(connection: Neo4jConnection, nodes: list[dict]) -> int:
    """Import Flow nodes into Neo4j."""
    if not nodes:
        return 0

    query = "UNWIND $batch AS props CREATE (f:Flow) SET f = props"
    total = 0
    for i in range(0, len(nodes), BATCH_SIZE):
        batch = nodes[i:i + BATCH_SIZE]
        with connection.session() as session:
            session.run(query, batch=batch)
        total += len(batch)
    logger.info("Imported %d Flow nodes", total)
    return total


def import_flow_edges(connection: Neo4jConnection, edges: list[dict]) -> int:
    """Import Flow edges into Neo4j, linking to existing Node and Flow nodes."""
    total = 0

    # Group by edge type
    by_type: dict[str, list[dict]] = {}
    for edge in edges:
        by_type.setdefault(edge["type"], []).append(edge)

    # FLOW_ENTRY: Flow -> Node (method)
    for edge in by_type.get("flow_entry", []):
        with connection.session() as session:
            session.run(
                """
                MATCH (f:Flow {flow_id: $flow_id})
                MATCH (n:Node {node_id: $node_id})
                CREATE (f)-[:FLOW_ENTRY]->(n)
                """,
                flow_id=edge["source_flow_id"],
                node_id=edge["target_node_id"],
            )
            total += 1

    # FLOW_STEP: Flow -> Node (class)
    for edge in by_type.get("flow_step", []):
        with connection.session() as session:
            session.run(
                """
                MATCH (f:Flow {flow_id: $flow_id})
                MATCH (n:Node {node_id: $node_id})
                CREATE (f)-[r:FLOW_STEP]->(n)
                SET r.position = $position, r.role = $role
                """,
                flow_id=edge["source_flow_id"],
                node_id=edge["target_node_id"],
                position=edge.get("position", 0),
                role=edge.get("role", ""),
            )
            total += 1

    # FLOW_TRIGGERS: Flow -> Flow
    for edge in by_type.get("flow_triggers", []):
        with connection.session() as session:
            session.run(
                """
                MATCH (f1:Flow {flow_id: $source_id})
                MATCH (f2:Flow {flow_id: $target_id})
                CREATE (f1)-[r:FLOW_TRIGGERS]->(f2)
                SET r.trigger_type = $trigger_type, r.via = $via
                """,
                source_id=edge["source_flow_id"],
                target_id=edge["target_flow_id"],
                trigger_type=edge.get("trigger_type", ""),
                via=edge.get("via", ""),
            )
            total += 1

    logger.info("Imported %d Flow edges", total)
    return total


def clear_flows(connection: Neo4jConnection) -> None:
    """Delete all Flow nodes and their relationships."""
    with connection.session() as session:
        session.run("MATCH (f:Flow) DETACH DELETE f")
    logger.info("Cleared all Flow nodes")
