"""Unit + integration tests for the simple-shape flow_importer.

Verifies acceptance criteria from docs/specs/flows-kloc-inteligence.md against
the canonical `kloc-reference-project-php/.kloc/symfony-kloc.json` fixture
(9 flows, 3 triggers, App-only).
"""

import copy
import json
import logging
from pathlib import Path

import pytest

from src.db.flow_importer import (
    clear_flows,
    import_flow_edges,
    import_flow_nodes,
    load_symfony_kloc,
    parse_flows,
)

from .conftest import requires_neo4j

REFERENCE_FIXTURE = Path(
    "/Users/michal/dev/ai/kloc/kloc-reference-project-php/.kloc/symfony-kloc.json"
)

ORDER_GET_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::get"
ORDER_GET_METHOD_NODE_ID = "node:779b5ec2e2f2e61b"

MESSAGE_HANDLER_FLOW_ID = "flow:message:App\\Ui\\Messenger\\Handler\\OrderCreatedHandler::__invoke"
ORDER_EVENT_FLOW_ID = (
    "flow:event:App\\Ui\\EventSubscriber\\OrderEventSubscriber::onOrderCreated[OrderCreatedEvent]"
)
PROCESS_ORDERS_FLOW_ID = "flow:cli:App\\Ui\\Console\\ProcessOrdersCommand::execute"


# ---------------------------------------------------------------------------
# Unit tests — parser layer, no Neo4j
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reference_data() -> dict:
    return load_symfony_kloc(REFERENCE_FIXTURE)


@pytest.fixture(scope="module")
def parsed(reference_data) -> tuple[list[dict], list[dict]]:
    return parse_flows(reference_data)


class TestParseFlowsReferenceProject:
    """Parse the canonical symfony-kloc.json and verify basic counts (AC-2..4 parser-layer)."""

    def test_parse_flows_reference_project_counts(self, parsed):
        nodes, _ = parsed
        assert len(nodes) == 9, f"Expected 9 flow nodes, got {len(nodes)}"

    def test_parse_flows_no_flow_step_edges(self, parsed):
        _, edges = parsed
        step_edges = [e for e in edges if e["type"] == "flow_step"]
        assert step_edges == [], "Parser must never emit flow_step edges"

    def test_parse_flows_trigger_count(self, parsed):
        _, edges = parsed
        triggers = [e for e in edges if e["type"] == "flow_triggers"]
        assert len(triggers) == 3, f"Expected 3 trigger edges, got {len(triggers)}"
        for t in triggers:
            assert t["trigger_type"] in {"event", "message"}
            assert t["via"].startswith("App\\")
            assert "source_flow_id" in t and "target_flow_id" in t

        # Spec D2 expected pairs (source × target × via)
        observed = {
            (t["source_flow_id"], t["target_flow_id"], t["trigger_type"], t["via"])
            for t in triggers
        }
        expected = {
            (
                "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create",
                ORDER_EVENT_FLOW_ID,
                "event",
                "App\\Event\\OrderCreatedEvent",
            ),
            (
                "flow:cli:App\\Ui\\Console\\ProcessReportsCommand::execute",
                "flow:event:App\\Ui\\EventSubscriber\\ReportEventSubscriber"
                "::onReportGenerated[ReportGeneratedEvent]",
                "event",
                "App\\Event\\ReportGeneratedEvent",
            ),
            (
                "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create",
                MESSAGE_HANDLER_FLOW_ID,
                "message",
                "App\\Ui\\Messenger\\Message\\OrderCreatedMessage",
            ),
        }
        assert observed == expected

    def test_parse_flows_filters_non_app(self, reference_data):
        synthetic = copy.deepcopy(reference_data)
        synthetic["flows"].append(
            {
                "id": "flow:http:Symfony\\Bundle\\FrameworkBundle\\Controller\\NotMineController::index",
                "type": "http",
                "entry": {
                    "fqn": "Symfony\\Bundle\\FrameworkBundle\\Controller\\NotMineController",
                    "method": "index",
                    "method_node_id": "node:framework",
                    "route": "/_framework/index",
                    "http_methods": ["GET"],
                },
            }
        )
        nodes, _ = parse_flows(synthetic)
        flow_ids = {n["flow_id"] for n in nodes}
        assert all(not fid.startswith("flow:http:Symfony\\") for fid in flow_ids)
        assert len(nodes) == 9


class TestParseFlowsNameDerivation:
    """Per-type :Flow.name derivation per spec D3a."""

    def _by_id(self, parsed) -> dict[str, dict]:
        nodes, _ = parsed
        return {n["flow_id"]: n for n in nodes}

    def test_parse_flows_http_name_derivation(self, parsed):
        node = self._by_id(parsed)[ORDER_GET_FLOW_ID]
        assert node["name"] == "GET /api/orders/{id}"

    def test_parse_flows_message_name_derivation(self, parsed):
        node = self._by_id(parsed)[MESSAGE_HANDLER_FLOW_ID]
        assert node["name"] == "OrderCreatedMessage"

    def test_parse_flows_event_name_derivation(self, parsed):
        node = self._by_id(parsed)[ORDER_EVENT_FLOW_ID]
        assert node["name"] == "OrderCreatedEvent"

    def test_parse_flows_cli_name_derivation(self, parsed):
        node = self._by_id(parsed)[PROCESS_ORDERS_FLOW_ID]
        assert node["name"] == "app:process-orders"


class TestParseFlowsPerTypeProps:
    """Each flow type carries its type-specific properties (spec D3)."""

    def _by_id(self, parsed) -> dict[str, dict]:
        nodes, _ = parsed
        return {n["flow_id"]: n for n in nodes}

    def test_parse_flows_http_props(self, parsed):
        node = self._by_id(parsed)[ORDER_GET_FLOW_ID]
        assert node["type"] == "http"
        assert node["route"] == "/api/orders/{id}"
        assert node["http_methods"] == ["GET"]
        assert "message_class" not in node
        assert "event_name" not in node
        assert "command_name" not in node

    def test_parse_flows_message_props(self, parsed):
        node = self._by_id(parsed)[MESSAGE_HANDLER_FLOW_ID]
        assert node["type"] == "message"
        assert node["message_class"] == "App\\Ui\\Messenger\\Message\\OrderCreatedMessage"
        assert "route" not in node
        assert "event_name" not in node
        assert "command_name" not in node

    def test_parse_flows_event_props(self, parsed):
        node = self._by_id(parsed)[ORDER_EVENT_FLOW_ID]
        assert node["type"] == "event"
        assert node["event_name"] == "App\\Event\\OrderCreatedEvent"
        assert "route" not in node
        assert "message_class" not in node
        assert "command_name" not in node

    def test_parse_flows_cli_props(self, parsed):
        node = self._by_id(parsed)[PROCESS_ORDERS_FLOW_ID]
        assert node["type"] == "cli"
        assert node["command_name"] == "app:process-orders"
        assert "route" not in node
        assert "message_class" not in node
        assert "event_name" not in node


# ---------------------------------------------------------------------------
# Integration tests — Neo4j round-trip
# ---------------------------------------------------------------------------


METHOD_NODE_IDS_FROM_REFERENCE = [
    "node:ea5b03126e94a456",
    "node:3c0761c151f9307c",
    "node:779b5ec2e2f2e61b",
    "node:bff993936de09915",
    "node:3e2c5405a8a4759e",
    "node:07a86921892cb4f3",
    "node:caef7a86b5bb2250",
    "node:6de4f3f68b8157d0",
    "node:45dfde229a8423b0",
]


def _seed_method_nodes(conn, node_ids: list[str]) -> None:
    """MERGE synthetic :Node stubs so FLOW_ENTRY edges can resolve."""
    with conn.session() as session:
        session.run(
            "UNWIND $ids AS nid MERGE (n:Node {node_id: nid})",
            ids=node_ids,
        )


def _count_flows(conn) -> int:
    with conn.session() as session:
        return session.run("MATCH (f:Flow) RETURN count(f) AS n").single()["n"]


def _count_rel(conn, rel_type: str) -> int:
    with conn.session() as session:
        return session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS n").single()["n"]


def _full_import(conn, data: dict) -> tuple[int, int, int]:
    """Run the full clear+nodes+edges pipeline and return (nodes, entry_edges, trig_edges)."""
    nodes, edges = parse_flows(data)
    clear_flows(conn)
    n_nodes = import_flow_nodes(conn, nodes)
    entry_edges = [e for e in edges if e["type"] == "flow_entry"]
    trig_edges = [e for e in edges if e["type"] == "flow_triggers"]
    n_entries = import_flow_edges(conn, entry_edges)
    n_triggers = import_flow_edges(conn, trig_edges)
    return n_nodes, n_entries, n_triggers


@requires_neo4j
class TestFlowImporterNeo4j:
    """Neo4j round-trip tests gated by the requires_neo4j marker."""

    @pytest.fixture(autouse=True)
    def _setup(self, loaded_database):
        """Seed the 9 reference :Node stubs and ensure :Flow state is clean per test."""
        self.conn = loaded_database
        _seed_method_nodes(self.conn, METHOD_NODE_IDS_FROM_REFERENCE)
        clear_flows(self.conn)
        yield
        clear_flows(self.conn)

    @pytest.fixture
    def reference_data(self) -> dict:
        return load_symfony_kloc(REFERENCE_FIXTURE)

    # --- Counts --------------------------------------------------------------

    def test_import_creates_9_flow_nodes(self, reference_data):
        n_nodes, _, _ = _full_import(self.conn, reference_data)
        assert n_nodes == 9
        assert _count_flows(self.conn) == 9

    def test_import_creates_9_flow_entry_edges(self, reference_data):
        _, n_entries, _ = _full_import(self.conn, reference_data)
        assert n_entries == 9
        assert _count_rel(self.conn, "FLOW_ENTRY") == 9

    def test_import_creates_3_flow_triggers_edges(self, reference_data):
        _, _, n_triggers = _full_import(self.conn, reference_data)
        assert n_triggers == 3
        assert _count_rel(self.conn, "FLOW_TRIGGERS") == 3

    def test_import_creates_zero_flow_step_edges(self, reference_data):
        _full_import(self.conn, reference_data)
        assert _count_rel(self.conn, "FLOW_STEP") == 0

    # --- Specific edges ------------------------------------------------------

    def test_flow_entry_resolves_to_correct_node(self, reference_data):
        _full_import(self.conn, reference_data)
        with self.conn.session() as session:
            result = session.run(
                "MATCH (f:Flow {flow_id: $fid})-[:FLOW_ENTRY]->(n:Node) RETURN n.node_id AS nid",
                fid=ORDER_GET_FLOW_ID,
            )
            record = result.single()
        assert record is not None, "FLOW_ENTRY edge missing for OrderController::get"
        assert record["nid"] == ORDER_GET_METHOD_NODE_ID

    def test_order_get_only_has_flow_entry_edge(self, reference_data):
        """AC-5 regression: from OrderController::get the only outgoing rel is FLOW_ENTRY."""
        _full_import(self.conn, reference_data)
        with self.conn.session() as session:
            rows = list(
                session.run(
                    "MATCH (:Flow {flow_id: $fid})-[r]->() "
                    "RETURN type(r) AS rel_type, count(r) AS cnt",
                    fid=ORDER_GET_FLOW_ID,
                )
            )
        assert len(rows) == 1
        assert rows[0]["rel_type"] == "FLOW_ENTRY"
        assert rows[0]["cnt"] == 1

    # --- Idempotency ---------------------------------------------------------

    def test_idempotent_double_import(self, reference_data):
        _full_import(self.conn, reference_data)
        _full_import(self.conn, reference_data)
        assert _count_flows(self.conn) == 9
        assert _count_rel(self.conn, "FLOW_ENTRY") == 9
        assert _count_rel(self.conn, "FLOW_TRIGGERS") == 3
        assert _count_rel(self.conn, "FLOW_STEP") == 0

    # --- Missing-node tolerance ---------------------------------------------

    def test_missing_method_node_warns_and_continues(self, reference_data, caplog):
        bad = copy.deepcopy(reference_data)
        target_flow = next(f for f in bad["flows"] if f["id"] == ORDER_GET_FLOW_ID)
        target_flow["entry"]["method_node_id"] = "node:doesnotexist000000"

        caplog.set_level(logging.WARNING, logger="src.db.flow_importer")
        n_nodes, n_entries, n_triggers = _full_import(self.conn, bad)

        # Flow node still created, but the bad FLOW_ENTRY is skipped
        assert n_nodes == 9
        assert _count_flows(self.conn) == 9
        assert n_entries == 8
        assert _count_rel(self.conn, "FLOW_ENTRY") == 8
        # Triggers untouched
        assert n_triggers == 3
        assert _count_rel(self.conn, "FLOW_TRIGGERS") == 3
        # OrderController::get exists but has no FLOW_ENTRY
        with self.conn.session() as session:
            rows = list(
                session.run(
                    "MATCH (:Flow {flow_id: $fid})-[r]->() RETURN type(r) AS t",
                    fid=ORDER_GET_FLOW_ID,
                )
            )
        assert rows == []
        # Warning logged for the missing node
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("node:doesnotexist000000" in r.getMessage() for r in warnings), (
            "expected a WARNING mentioning the missing node_id"
        )

    # --- Property contract --------------------------------------------------

    def test_http_flow_node_properties(self, reference_data):
        _full_import(self.conn, reference_data)
        with self.conn.session() as session:
            record = session.run(
                "MATCH (f:Flow {flow_id: $fid}) RETURN f AS flow",
                fid=ORDER_GET_FLOW_ID,
            ).single()
        assert record is not None
        flow = dict(record["flow"])

        # Required keys per spec D3
        for k in ("flow_id", "type", "entry_fqn", "entry_method", "name", "route", "http_methods"):
            assert k in flow, f"missing required key {k!r} on http :Flow"
        assert flow["type"] == "http"
        assert flow["name"] == "GET /api/orders/{id}"
        assert flow["route"] == "/api/orders/{id}"
        assert list(flow["http_methods"]) == ["GET"]

        # Forbidden enrichment keys must NEVER appear (AC-14)
        for k in ("explanation_business", "explanation_technical", "explanation_search"):
            assert k not in flow, f"forbidden key {k!r} present on :Flow"
