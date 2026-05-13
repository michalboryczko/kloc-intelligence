"""Unit + integration tests for the v3 flow importer.

Drives the canonical v3 fixture at
``kloc-symfony/contract-tests/output/symfony-kloc.json`` (10 flows, 2 messages,
2 events, 1 http_client). Covers parser counts, the four-label MERGE-reconcile
flow, idempotency including ``:Flow.explanation`` preservation, the Qdrant
filter-delete contract, and legacy ``FLOW_TRIGGERS`` sweep behaviour.

Stream-1 acceptance criteria covered: #1-#16 (counts), #17-#22 (idempotency),
#23-#25 (edge cases), #38-#39 (schema).
"""

import copy
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from src.db.flow_importer import (
    ImportReport,
    _build_set_clause,
    _bulk_replace_edges,
    _delete_legacy_flow_triggers,
    _reconcile_nodes,
    load_symfony_kloc,
    parse_v3,
    run_import,
)

from .conftest import REFERENCE_SYMFONY_KLOC_V3 as REFERENCE_FIXTURE
from .conftest import requires_neo4j

pytestmark = pytest.mark.skipif(
    not REFERENCE_FIXTURE.is_file(),
    reason=f"Reference v3 symfony-kloc.json not available at {REFERENCE_FIXTURE}",
)


# Canonical reference oracle (from .claude/qa-notes/symfony-v3_qa_ref_note.md)
FLOW_IDS = [
    "flow:http:App\\Ui\\Rest\\Controller\\CustomerController::get",
    "flow:http:App\\Ui\\Rest\\Controller\\CustomerController::summary",
    "flow:http:App\\Ui\\Rest\\Controller\\OrderController::get",
    "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create",
    "flow:http:App\\Ui\\Rest\\Controller\\PaymentController::verify",
    "flow:message:App\\Ui\\Messenger\\Handler\\OrderCreatedHandler::__invoke",
    "flow:event:App\\Ui\\EventSubscriber\\OrderEventSubscriber::onOrderCreated[OrderCreatedEvent]",
    "flow:event:App\\Ui\\EventSubscriber\\ReportEventSubscriber"
    "::onReportGenerated[ReportGeneratedEvent]",
    "flow:cli:App\\Ui\\Console\\ProcessOrdersCommand::execute",
    "flow:cli:App\\Ui\\Console\\ProcessReportsCommand::execute",
]
ORDER_CREATE_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"
ORDER_GET_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::get"
PAYMENT_VERIFY_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\PaymentController::verify"
MESSAGE_HANDLER_FLOW_ID = "flow:message:App\\Ui\\Messenger\\Handler\\OrderCreatedHandler::__invoke"
ORDER_EVENT_FLOW_ID = (
    "flow:event:App\\Ui\\EventSubscriber\\OrderEventSubscriber::onOrderCreated[OrderCreatedEvent]"
)
PROCESS_ORDERS_FLOW_ID = "flow:cli:App\\Ui\\Console\\ProcessOrdersCommand::execute"

ORDER_CREATED_MESSAGE_ID = "message:App\\Ui\\Messenger\\Message\\OrderCreatedMessage"
AUDIT_LOG_MESSAGE_ID = "message:App\\Ui\\Messenger\\Message\\AuditLogMessage"
ORDER_CREATED_EVENT_ID = "event:App\\Event\\OrderCreatedEvent"
REPORT_GENERATED_EVENT_ID = "event:App\\Event\\ReportGeneratedEvent"
PAYPAL_HTTP_CLIENT_ID = "http_client:paypal.client"

# class_node_id targets in the canonical fixture (Stream 1 needs these to seed
# fake :Node stubs for OF_TYPE edges during integration tests)
CLASS_NODE_IDS = [
    "node:9478ce52b7e491e8",  # CustomerController
    "node:fa228897463697cb",  # OrderController
    "node:fb2cf7de19c15eba",  # PaymentController
    "node:1b802111ec29ea26",  # OrderCreatedHandler
    "node:90c19c50267d0e1c",  # OrderEventSubscriber
    "node:fbc7deb2499b5fc4",  # ReportEventSubscriber
    "node:3188ce1e8c6ebfe6",  # ProcessOrdersCommand
    "node:fec6dd46c35d6389",  # ProcessReportsCommand
    "node:59e968d18f1e34ce",  # AuditLogMessage class
    "node:72b6d826c2ab77ae",  # OrderCreatedMessage class
    "node:0f7b1ffca5b17133",  # OrderCreatedEvent class
    "node:facb07024b43be8e",  # ReportGeneratedEvent class
]

METHOD_NODE_IDS = [
    "node:b602e89136cdb2eb",
    "node:6af85a1102294e06",
    "node:eadaddc82ce9f4da",
    "node:b2709b1ff6ea5c0d",
    "node:204b581dfd2fe598",
    "node:a89ec182868ede0e",
    "node:9c32a48fbad5415f",
    "node:47f4fdac86fd4890",
    "node:849473e11897396b",
    "node:3c3776b37876d41c",
]

CALL_NODE_IDS = [
    "node:call:7cfbfd219476df9c",
    "node:call:21a607e12e155bb6",
    "node:call:e7716e5498131cff",
    "node:call:cae6070e97c962dd",
    "node:call:0cadc799cb94ee70",
]


# ---------------------------------------------------------------------------
# Parser-layer unit tests (no Neo4j)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def v3_data() -> dict:
    return load_symfony_kloc(REFERENCE_FIXTURE)


@pytest.fixture(scope="module")
def v3_parsed(v3_data):
    return parse_v3(v3_data)


class TestParseV3Counts:
    """Node-set counts straight from the parser layer."""

    def test_flow_count(self, v3_parsed):
        flows, _, _, _, _ = v3_parsed
        assert len(flows) == 10

    def test_message_count(self, v3_parsed):
        _, messages, _, _, _ = v3_parsed
        assert len(messages) == 2

    def test_event_count(self, v3_parsed):
        _, _, events, _, _ = v3_parsed
        assert len(events) == 2

    def test_http_client_count(self, v3_parsed):
        _, _, _, http_clients, _ = v3_parsed
        assert len(http_clients) == 1

    def test_flow_ids_match_oracle(self, v3_parsed):
        flows, _, _, _, _ = v3_parsed
        assert {f["flow_id"] for f in flows} == set(FLOW_IDS)


class TestParseV3Edges:
    """All twelve edge variants land in ``edges`` with the documented shape."""

    def test_flow_entry_edges(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["flow_entry"]) == 10
        for e in edges["flow_entry"]:
            assert {"flow_id", "method_node_id"} <= e.keys()

    def test_flow_entry_class_edges(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["flow_entry_class"]) == 10
        for e in edges["flow_entry_class"]:
            assert {"flow_id", "class_node_id"} <= e.keys()

    def test_emits_message_flow(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["emits_message_flow"]) == 2
        keys = {
            "flow_id",
            "message_id",
            "call_node_id",
            "caller_method_fqn",
            "caller_method_node_id",
        }
        for e in edges["emits_message_flow"]:
            assert keys <= e.keys()

    def test_emits_message_call(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["emits_message_call"]) == 2

    def test_emits_event_flow(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["emits_event_flow"]) == 2

    def test_emits_event_call(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["emits_event_call"]) == 2

    def test_uses_http_flow(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["uses_http_flow"]) == 1

    def test_uses_http_call(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["uses_http_call"]) == 1

    def test_handled_by_message_only_for_targeted(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        # AuditLogMessage has empty targets so only OrderCreatedMessage produces an edge
        assert len(edges["handled_by_message"]) == 1
        assert edges["handled_by_message"][0]["message_id"] == ORDER_CREATED_MESSAGE_ID

    def test_handled_by_event_carries_priority(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["handled_by_event"]) == 2
        for e in edges["handled_by_event"]:
            assert e["priority"] == 0

    def test_of_type_message_when_class_node_resolves(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["of_type_message"]) == 2

    def test_of_type_event_when_class_node_resolves(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        assert len(edges["of_type_event"]) == 2

    def test_of_type_http_client_skips_null_class(self, v3_parsed):
        _, _, _, _, edges = v3_parsed
        # paypal.client has class_node_id: null → no OF_TYPE edge
        assert edges["of_type_http_client"] == []


class TestParseV3FlowProperties:
    """:Flow per-type property contract and name derivation."""

    def _by_id(self, v3_parsed) -> dict[str, dict]:
        flows, _, _, _, _ = v3_parsed
        return {f["flow_id"]: f for f in flows}

    def test_http_props(self, v3_parsed):
        node = self._by_id(v3_parsed)[ORDER_GET_FLOW_ID]
        assert node["type"] == "http"
        assert node["name"] == "GET /api/orders/{id}"
        assert node["route"] == "/api/orders/{id}"
        assert node["http_methods"] == ["GET"]
        for k in ("message_class", "event_name", "command_name"):
            assert k not in node

    def test_message_props(self, v3_parsed):
        node = self._by_id(v3_parsed)[MESSAGE_HANDLER_FLOW_ID]
        assert node["type"] == "message"
        assert node["name"] == "OrderCreatedMessage"
        assert node["message_class"] == "App\\Ui\\Messenger\\Message\\OrderCreatedMessage"
        for k in ("route", "http_methods", "event_name", "command_name"):
            assert k not in node

    def test_event_props(self, v3_parsed):
        node = self._by_id(v3_parsed)[ORDER_EVENT_FLOW_ID]
        assert node["type"] == "event"
        assert node["name"] == "OrderCreatedEvent"
        assert node["event_name"] == "App\\Event\\OrderCreatedEvent"
        for k in ("route", "http_methods", "message_class", "command_name"):
            assert k not in node

    def test_cli_props(self, v3_parsed):
        node = self._by_id(v3_parsed)[PROCESS_ORDERS_FLOW_ID]
        assert node["type"] == "cli"
        assert node["name"] == "app:process-orders"
        assert node["command_name"] == "app:process-orders"
        for k in ("route", "http_methods", "message_class", "event_name"):
            assert k not in node


class TestParseV3MessageProperties:
    def test_audit_log_message(self, v3_parsed):
        _, messages, _, _, _ = v3_parsed
        by_id = {m["id"]: m for m in messages}
        audit = by_id[AUDIT_LOG_MESSAGE_ID]
        assert audit["fqn"] == "App\\Ui\\Messenger\\Message\\AuditLogMessage"
        assert audit["transports"] == []

    def test_order_created_message_transports(self, v3_parsed):
        _, messages, _, _, _ = v3_parsed
        by_id = {m["id"]: m for m in messages}
        order = by_id[ORDER_CREATED_MESSAGE_ID]
        assert order["transports"] == ["sync"]


class TestParseV3HttpClientProperties:
    def test_paypal_client(self, v3_parsed):
        _, _, _, http_clients, _ = v3_parsed
        client = http_clients[0]
        assert client["id"] == PAYPAL_HTTP_CLIENT_ID
        assert client["service_id"] == "paypal.client"
        assert client["base_uri"] == "https://api.paypal.com"
        assert client["class_fqn"] == "Symfony\\Component\\HttpClient\\UriTemplateHttpClient"


class TestParseV3AppNamespaceFilter:
    def test_non_app_flow_dropped(self, v3_data):
        synthetic = copy.deepcopy(v3_data)
        synthetic["flows"].append(
            {
                "id": "flow:http:Symfony\\Bundle\\NotMineController::index",
                "type": "http",
                "entry": {
                    "fqn": "Symfony\\Bundle\\NotMineController",
                    "node_id": "node:framework_cls",
                    "method": "index",
                    "method_node_id": "node:framework_method",
                    "route": "/_framework/index",
                    "http_methods": ["GET"],
                },
            }
        )
        flows, _, _, _, _ = parse_v3(synthetic)
        ids = {f["flow_id"] for f in flows}
        assert len(flows) == 10
        assert all(not fid.startswith("flow:http:Symfony") for fid in ids)

    def test_messages_are_not_namespace_filtered(self, v3_data):
        # Messages/events/http_clients are universal — App filter applies only to flows.
        synthetic = copy.deepcopy(v3_data)
        synthetic["messages"].append(
            {
                "id": "message:Vendor\\SomeMessage",
                "dispatched_class": "Vendor\\SomeMessage",
                "dispatched_class_node_id": None,
                "transports": [],
                "sources": [],
                "targets": [],
            }
        )
        _, messages, _, _, _ = parse_v3(synthetic)
        assert len(messages) == 3


# ---------------------------------------------------------------------------
# Reconcile / SET-clause helpers — unit tests
# ---------------------------------------------------------------------------


class TestBuildSetClause:
    def test_empty(self):
        assert _build_set_clause([]) == ""

    def test_single_key(self):
        assert _build_set_clause(["name"]) == "SET n.name = props.name"

    def test_multiple_keys(self):
        out = _build_set_clause(["a", "b", "c"])
        assert out == "SET n.a = props.a, n.b = props.b, n.c = props.c"

    def test_rejects_unsafe_keys(self):
        with pytest.raises(ValueError):
            _build_set_clause(["valid", "drop-table"])


# ---------------------------------------------------------------------------
# Qdrant helper unit tests — never delete the collection
# ---------------------------------------------------------------------------


class TestQdrantFilterDelete:
    def test_uses_filter_selector_with_flow_id_match(self):
        from src.ai import flow_qdrant

        fake_client = MagicMock()
        fake_client.scroll.return_value = ([MagicMock(id="pt-1")], None)

        with patch("qdrant_client.QdrantClient", return_value=fake_client):
            count = flow_qdrant.delete_flow_embedding("flow:test", "http://localhost:6333")

        assert count == 1
        # delete() must have been called and never delete_collection()
        assert fake_client.delete.called
        assert not fake_client.delete_collection.called

        call_args = fake_client.delete.call_args
        assert call_args.kwargs["collection_name"] == "flow_explain_embeddings"

        from qdrant_client.http.models import FieldCondition, FilterSelector, MatchValue

        selector = call_args.kwargs["points_selector"]
        assert isinstance(selector, FilterSelector)
        flt = selector.filter
        assert len(flt.must) == 1
        cond = flt.must[0]
        assert isinstance(cond, FieldCondition)
        assert cond.key == "flow_id"
        assert isinstance(cond.match, MatchValue)
        assert cond.match.value == "flow:test"

    def test_list_flow_point_ids_returns_ids(self):
        from src.ai import flow_qdrant

        fake_client = MagicMock()
        fake_client.scroll.return_value = (
            [MagicMock(id="pt-1"), MagicMock(id="pt-2")],
            None,
        )
        with patch("qdrant_client.QdrantClient", return_value=fake_client):
            ids = flow_qdrant.list_flow_point_ids("flow:test", "http://localhost:6333")
        assert ids == ["pt-1", "pt-2"]
        assert fake_client.scroll.called
        kwargs = fake_client.scroll.call_args.kwargs
        assert kwargs["with_payload"] is False
        assert kwargs["with_vectors"] is False

    def test_delete_flow_embedding_handles_missing_collection(self, caplog):
        from src.ai import flow_qdrant

        fake_client = MagicMock()
        fake_client.scroll.side_effect = Exception("collection not found (404)")
        fake_client.delete.side_effect = Exception("collection not found (404)")

        with patch("qdrant_client.QdrantClient", return_value=fake_client):
            caplog.set_level(logging.WARNING, logger="src.ai.flow_qdrant")
            count = flow_qdrant.delete_flow_embedding("flow:any", "http://localhost:6333")

        assert count == 0
        assert not fake_client.delete_collection.called
        assert any("missing" in r.getMessage() for r in caplog.records)

    def test_delete_collection_never_called_by_flow_importer(self):
        # Importer must never invoke client.delete_collection — regression guard
        from pathlib import Path

        src = Path(__file__).parent.parent / "src" / "db" / "flow_importer.py"
        text = src.read_text()
        assert "delete_collection" not in text


# ---------------------------------------------------------------------------
# Neo4j integration tests
# ---------------------------------------------------------------------------


def _count_label(conn, label: str) -> int:
    with conn.session() as session:
        return session.run(f"MATCH (n:{label}) RETURN count(n) AS n").single()["n"]


def _count_rel_between(conn, src_label: str, rel_type: str, dst_label: str | None = None) -> int:
    dst = f":{dst_label}" if dst_label else ""
    with conn.session() as session:
        return session.run(
            f"MATCH (:{src_label})-[r:{rel_type}]->({dst}) RETURN count(r) AS n"
        ).single()["n"]


def _count_rel_total(conn, rel_type: str) -> int:
    with conn.session() as session:
        return session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS n").single()["n"]


def _seed_stub_nodes(conn, node_ids: list[str], extra_labels: list[str] | None = None) -> None:
    """MERGE synthetic :Node stubs (optionally with extra labels) so edges can resolve."""
    with conn.session() as session:
        session.run(
            "UNWIND $ids AS nid MERGE (n:Node {node_id: nid})",
            ids=node_ids,
        )
        for label in extra_labels or []:
            session.run(
                f"UNWIND $ids AS nid MATCH (n:Node {{node_id: nid}}) SET n:{label}",
                ids=node_ids,
            )


def _seed_call_nodes(conn, node_ids: list[str]) -> None:
    """:Call nodes carry both :Node and :Call labels per the SoT importer."""
    if not node_ids:
        return
    with conn.session() as session:
        session.run(
            "UNWIND $ids AS nid MERGE (n:Node {node_id: nid}) SET n:Call",
            ids=node_ids,
        )


def _clear_v3_state(conn) -> None:
    """Detach-delete the four v3 label sets between tests so each starts clean."""
    with conn.session() as session:
        session.run(
            "MATCH (n) WHERE n:Flow OR n:Message OR n:Event OR n:HttpClient DETACH DELETE n"
        )
        session.run("MATCH ()-[r:FLOW_TRIGGERS]->() DELETE r")


@requires_neo4j
class TestRunImportReferenceCounts:
    """Spec ACs #1-#16 — counts after a clean import of the canonical v3 fixture."""

    @pytest.fixture(autouse=True)
    def _setup(self, loaded_database):
        self.conn = loaded_database
        _seed_stub_nodes(self.conn, METHOD_NODE_IDS, extra_labels=["Method"])
        _seed_stub_nodes(self.conn, CLASS_NODE_IDS, extra_labels=["Class"])
        _seed_call_nodes(self.conn, CALL_NODE_IDS)
        _clear_v3_state(self.conn)
        self.report = run_import(self.conn, REFERENCE_FIXTURE)
        yield
        _clear_v3_state(self.conn)

    def test_ac1_flow_count(self):
        assert _count_label(self.conn, "Flow") == 10

    def test_ac2_message_count(self):
        assert _count_label(self.conn, "Message") == 2

    def test_ac3_event_count(self):
        assert _count_label(self.conn, "Event") == 2

    def test_ac4_http_client_count(self):
        assert _count_label(self.conn, "HttpClient") == 1

    def test_ac5_flow_entry_count(self):
        assert _count_rel_between(self.conn, "Flow", "FLOW_ENTRY") == 10

    def test_ac6_flow_entry_class_count(self):
        assert _count_rel_between(self.conn, "Flow", "FLOW_ENTRY_CLASS") == 10

    def test_ac7_flow_emits_count(self):
        assert _count_rel_between(self.conn, "Flow", "EMITS") == 4

    def test_ac8_call_emits_count(self):
        assert _count_rel_between(self.conn, "Call", "EMITS") == 4

    def test_ac9_flow_uses_http_client_count(self):
        assert _count_rel_between(self.conn, "Flow", "USES_HTTP_CLIENT") == 1

    def test_ac10_call_uses_http_client_count(self):
        assert _count_rel_between(self.conn, "Call", "USES_HTTP_CLIENT") == 1

    def test_ac11_message_handled_by_count(self):
        assert _count_rel_between(self.conn, "Message", "HANDLED_BY", "Flow") == 1
        # AuditLogMessage must NOT have a HANDLED_BY edge
        with self.conn.session() as session:
            count = session.run(
                "MATCH (:Message {id: $id})-[r:HANDLED_BY]->() RETURN count(r) AS n",
                id=AUDIT_LOG_MESSAGE_ID,
            ).single()["n"]
        assert count == 0

    def test_ac12_event_handled_by_count(self):
        assert _count_rel_between(self.conn, "Event", "HANDLED_BY", "Flow") == 2
        with self.conn.session() as session:
            rows = list(
                session.run("MATCH (:Event)-[r:HANDLED_BY]->(:Flow) RETURN r.priority AS p")
            )
        assert all(r["p"] == 0 for r in rows)

    def test_ac13_message_of_type_count(self):
        assert _count_rel_between(self.conn, "Message", "OF_TYPE") == 2

    def test_ac14_event_of_type_count(self):
        assert _count_rel_between(self.conn, "Event", "OF_TYPE") == 2

    def test_ac15_http_client_of_type_count_zero_for_vendor(self):
        assert _count_rel_between(self.conn, "HttpClient", "OF_TYPE") == 0

    def test_ac16_no_flow_triggers_edges(self):
        assert _count_rel_total(self.conn, "FLOW_TRIGGERS") == 0

    def test_import_report_matches_neo4j(self):
        r = self.report
        assert r.flows_upserted == 10
        assert r.messages_upserted == 2
        assert r.events_upserted == 2
        assert r.http_clients_upserted == 1
        assert r.flow_entry_edges == 10
        assert r.flow_entry_class_edges == 10
        assert r.emits_flow_message_edges == 2
        assert r.emits_flow_event_edges == 2
        assert r.emits_call_message_edges == 2
        assert r.emits_call_event_edges == 2
        assert r.uses_http_flow_edges == 1
        assert r.uses_http_call_edges == 1
        assert r.handled_by_message_edges == 1
        assert r.handled_by_event_edges == 2
        assert r.of_type_message_edges == 2
        assert r.of_type_event_edges == 2
        assert r.of_type_http_edges == 0
        assert r.legacy_flow_triggers_deleted == 0


@requires_neo4j
class TestRunImportIdempotency:
    """Spec ACs #17-#22 — re-import preserves enrichment props and respects Qdrant."""

    @pytest.fixture(autouse=True)
    def _setup(self, loaded_database):
        self.conn = loaded_database
        _seed_stub_nodes(self.conn, METHOD_NODE_IDS, extra_labels=["Method"])
        _seed_stub_nodes(self.conn, CLASS_NODE_IDS, extra_labels=["Class"])
        _seed_call_nodes(self.conn, CALL_NODE_IDS)
        _clear_v3_state(self.conn)
        yield
        _clear_v3_state(self.conn)

    def test_ac17_double_import_counts_unchanged(self):
        run_import(self.conn, REFERENCE_FIXTURE)
        run_import(self.conn, REFERENCE_FIXTURE)

        assert _count_label(self.conn, "Flow") == 10
        assert _count_label(self.conn, "Message") == 2
        assert _count_label(self.conn, "Event") == 2
        assert _count_label(self.conn, "HttpClient") == 1
        assert _count_rel_between(self.conn, "Flow", "FLOW_ENTRY") == 10
        assert _count_rel_between(self.conn, "Flow", "FLOW_ENTRY_CLASS") == 10
        assert _count_rel_between(self.conn, "Flow", "EMITS") == 4
        assert _count_rel_between(self.conn, "Call", "EMITS") == 4
        assert _count_rel_between(self.conn, "Flow", "USES_HTTP_CLIENT") == 1
        assert _count_rel_between(self.conn, "Call", "USES_HTTP_CLIENT") == 1
        assert _count_rel_between(self.conn, "Message", "HANDLED_BY", "Flow") == 1
        assert _count_rel_between(self.conn, "Event", "HANDLED_BY", "Flow") == 2

    def test_ac18_explanation_survives_reimport(self):
        run_import(self.conn, REFERENCE_FIXTURE)
        with self.conn.session() as session:
            session.run(
                "MATCH (f:Flow {flow_id: $fid}) "
                "SET f.explanation = $expl, f.explain_model = $model, "
                "f.explain_at = datetime()",
                fid=ORDER_CREATE_FLOW_ID,
                expl="QA-CANARY-EXPLANATION",
                model="qa-model",
            )

        run_import(self.conn, REFERENCE_FIXTURE)

        with self.conn.session() as session:
            row = session.run(
                "MATCH (f:Flow {flow_id: $fid}) "
                "RETURN f.explanation AS expl, f.explain_model AS m, f.explain_at AS at",
                fid=ORDER_CREATE_FLOW_ID,
            ).single()
        assert row["expl"] == "QA-CANARY-EXPLANATION"
        assert row["m"] == "qa-model"
        assert row["at"] is not None

    def test_ac19_orphan_flow_triggers_qdrant_filter_delete(self, tmp_path):
        run_import(self.conn, REFERENCE_FIXTURE)
        data = load_symfony_kloc(REFERENCE_FIXTURE)
        data["flows"] = [f for f in data["flows"] if f["id"] != PAYMENT_VERIFY_FLOW_ID]
        data["messages"] = [m for m in data["messages"] if m["id"] != AUDIT_LOG_MESSAGE_ID]
        data["http_clients"] = []
        modified = tmp_path / "v3_minus_payment.json"
        modified.write_text(json.dumps(data))

        with patch("src.ai.flow_qdrant.delete_flow_embedding") as mock_delete:
            mock_delete.return_value = 3
            report = run_import(
                self.conn,
                modified,
                qdrant_url="http://localhost:6333",
            )

        assert mock_delete.called
        called_with_payment = any(
            call.args[0] == PAYMENT_VERIFY_FLOW_ID for call in mock_delete.call_args_list
        )
        assert called_with_payment
        assert _count_label(self.conn, "Flow") == 9
        with self.conn.session() as session:
            count = session.run(
                "MATCH (f:Flow {flow_id: $fid}) RETURN count(f) AS n",
                fid=PAYMENT_VERIFY_FLOW_ID,
            ).single()["n"]
        assert count == 0
        assert report.flows_deleted == 1
        assert PAYMENT_VERIFY_FLOW_ID in report.deleted_flow_ids
        assert report.qdrant_points_deleted >= 0  # mock returned 3 per delete call

    def test_ac19_no_qdrant_call_when_url_absent(self, tmp_path):
        run_import(self.conn, REFERENCE_FIXTURE)
        data = load_symfony_kloc(REFERENCE_FIXTURE)
        data["flows"] = [f for f in data["flows"] if f["id"] != PAYMENT_VERIFY_FLOW_ID]
        data["messages"] = [m for m in data["messages"] if m["id"] != AUDIT_LOG_MESSAGE_ID]
        data["http_clients"] = []
        modified = tmp_path / "v3_minus_payment.json"
        modified.write_text(json.dumps(data))

        with patch("src.ai.flow_qdrant.delete_flow_embedding") as mock_delete:
            report = run_import(self.conn, modified, qdrant_url=None)

        assert not mock_delete.called
        assert report.qdrant_points_deleted == 0
        assert report.flows_deleted == 1

    def test_ac21_legacy_flow_triggers_swept(self):
        with self.conn.session() as session:
            session.run(
                "MERGE (a:Flow {flow_id: 'legacy:a'}) "
                "MERGE (b:Flow {flow_id: 'legacy:b'}) "
                "MERGE (a)-[:FLOW_TRIGGERS {via: 'legacy'}]->(b)"
            )
        assert _count_rel_total(self.conn, "FLOW_TRIGGERS") == 1

        run_import(self.conn, REFERENCE_FIXTURE)

        assert _count_rel_total(self.conn, "FLOW_TRIGGERS") == 0


@requires_neo4j
class TestRunImportEdgeCases:
    """Spec ACs #23-#25 — edge cases the importer must tolerate."""

    @pytest.fixture(autouse=True)
    def _setup(self, loaded_database):
        self.conn = loaded_database
        _seed_stub_nodes(self.conn, METHOD_NODE_IDS, extra_labels=["Method"])
        _seed_stub_nodes(self.conn, CLASS_NODE_IDS, extra_labels=["Class"])
        _seed_call_nodes(self.conn, CALL_NODE_IDS)
        _clear_v3_state(self.conn)
        yield
        _clear_v3_state(self.conn)

    def test_ac23_message_with_empty_targets(self):
        run_import(self.conn, REFERENCE_FIXTURE)
        with self.conn.session() as session:
            row = session.run(
                "MATCH (m:Message {id: $id}) "
                "OPTIONAL MATCH (m)-[r:HANDLED_BY]->() "
                "WITH m, count(r) AS handler_cnt "
                "OPTIONAL MATCH (f:Flow)-[em:EMITS]->(m) "
                "RETURN m.id AS id, handler_cnt, count(em) AS emit_cnt",
                id=AUDIT_LOG_MESSAGE_ID,
            ).single()
        assert row["id"] == AUDIT_LOG_MESSAGE_ID
        assert row["handler_cnt"] == 0
        assert row["emit_cnt"] == 1

    def test_ac24_http_client_with_null_class_node_id(self):
        run_import(self.conn, REFERENCE_FIXTURE)
        with self.conn.session() as session:
            row = session.run(
                "MATCH (h:HttpClient {id: $id}) "
                "OPTIONAL MATCH (h)-[r:OF_TYPE]->() "
                "RETURN h.class_fqn AS cls, count(r) AS of_type_cnt",
                id=PAYPAL_HTTP_CLIENT_ID,
            ).single()
        assert row["cls"] == "Symfony\\Component\\HttpClient\\UriTemplateHttpClient"
        assert row["of_type_cnt"] == 0

    def test_ac25_missing_method_node_id_tolerated(self, tmp_path, caplog):
        data = load_symfony_kloc(REFERENCE_FIXTURE)
        data["flows"][0]["entry"]["method_node_id"] = "node:doesnotexist000000"
        modified = tmp_path / "v3_bad_method_node.json"
        modified.write_text(json.dumps(data))

        caplog.set_level(logging.WARNING, logger="src.db.flow_importer")
        report = run_import(self.conn, modified)

        assert report.flows_upserted == 10
        assert _count_label(self.conn, "Flow") == 10
        # FLOW_ENTRY edges: 9 (the bad one is unresolved)
        assert _count_rel_between(self.conn, "Flow", "FLOW_ENTRY") == 9
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("FLOW_ENTRY" in r.getMessage() for r in warnings)


@requires_neo4j
class TestRunImportLegacyAndEmpty:
    """Additional reconcile-path checks: empty body and legacy cleanup idempotency."""

    @pytest.fixture(autouse=True)
    def _setup(self, loaded_database):
        self.conn = loaded_database
        _seed_stub_nodes(self.conn, METHOD_NODE_IDS, extra_labels=["Method"])
        _seed_stub_nodes(self.conn, CLASS_NODE_IDS, extra_labels=["Class"])
        _seed_call_nodes(self.conn, CALL_NODE_IDS)
        _clear_v3_state(self.conn)
        yield
        _clear_v3_state(self.conn)

    def test_empty_body_reconciles_all_to_zero(self, tmp_path):
        run_import(self.conn, REFERENCE_FIXTURE)
        empty = tmp_path / "empty.json"
        empty.write_text("{}")

        report = run_import(self.conn, empty)
        assert _count_label(self.conn, "Flow") == 0
        assert _count_label(self.conn, "Message") == 0
        assert _count_label(self.conn, "Event") == 0
        assert _count_label(self.conn, "HttpClient") == 0
        assert report.flows_deleted == 10
        assert report.messages_deleted == 2
        assert report.events_deleted == 2
        assert report.http_clients_deleted == 1

    def test_legacy_cleanup_helper_is_idempotent(self):
        _delete_legacy_flow_triggers(self.conn)
        _delete_legacy_flow_triggers(self.conn)


@requires_neo4j
class TestReconcileHelpers:
    """White-box tests for _reconcile_nodes preservation semantics and _bulk_replace_edges."""

    @pytest.fixture(autouse=True)
    def _setup(self, loaded_database):
        self.conn = loaded_database
        _clear_v3_state(self.conn)
        yield
        _clear_v3_state(self.conn)

    def test_reconcile_preserves_explanation_on_flow(self):
        with self.conn.session() as session:
            session.run(
                "MERGE (f:Flow {flow_id: 'flow:test'}) "
                "SET f.explanation = 'KEEP', f.explain_model = 'm', "
                "f.name = 'old-name'"
            )
        upserted, deleted = _reconcile_nodes(
            self.conn,
            "Flow",
            "flow_id",
            [{"flow_id": "flow:test", "name": "new-name", "type": "http"}],
            preserve_props={"explanation", "explain_model", "explain_at"},
        )
        assert upserted == 1
        assert deleted == []
        with self.conn.session() as session:
            row = session.run(
                "MATCH (f:Flow {flow_id: 'flow:test'}) "
                "RETURN f.explanation AS expl, f.name AS name, f.type AS t"
            ).single()
        assert row["expl"] == "KEEP"
        assert row["name"] == "new-name"
        assert row["t"] == "http"

    def test_reconcile_deletes_orphans(self):
        with self.conn.session() as session:
            session.run("MERGE (m:Message {id: 'a'})")
            session.run("MERGE (m:Message {id: 'b'})")
        upserted, deleted = _reconcile_nodes(
            self.conn,
            "Message",
            "id",
            [{"id": "a", "fqn": "X"}],
        )
        assert upserted == 1
        assert sorted(deleted) == ["b"]
        assert _count_label(self.conn, "Message") == 1

    def test_bulk_replace_edges_replaces_atomically(self):
        with self.conn.session() as session:
            session.run("MERGE (f:Flow {flow_id: 'f1'})")
            session.run("MERGE (f:Flow {flow_id: 'f2'})")
            session.run("MERGE (m:Message {id: 'm1'})")
            session.run("MERGE (m:Message {id: 'm2'})")

        created = _bulk_replace_edges(
            self.conn,
            edge_label="EMITS",
            from_label="Flow",
            from_id_key="flow_id",
            to_label="Message",
            to_id_key="id",
            edges=[
                {"flow_id": "f1", "message_id": "m1", "call_node_id": "c1"},
            ],
            from_field="flow_id",
            to_field="message_id",
            prop_fields=["call_node_id"],
        )
        assert created == 1

        # Re-run with a different desired set: old edge must be removed.
        created2 = _bulk_replace_edges(
            self.conn,
            edge_label="EMITS",
            from_label="Flow",
            from_id_key="flow_id",
            to_label="Message",
            to_id_key="id",
            edges=[
                {"flow_id": "f2", "message_id": "m2", "call_node_id": "c2"},
            ],
            from_field="flow_id",
            to_field="message_id",
            prop_fields=["call_node_id"],
        )
        assert created2 == 1
        assert _count_rel_between(self.conn, "Flow", "EMITS", "Message") == 1
        with self.conn.session() as session:
            rows = list(
                session.run(
                    "MATCH (f:Flow)-[r:EMITS]->(m:Message) "
                    "RETURN f.flow_id AS src, m.id AS dst, r.call_node_id AS c"
                )
            )
        assert rows == [{"src": "f2", "dst": "m2", "c": "c2"}]


# ---------------------------------------------------------------------------
# ImportReport sanity
# ---------------------------------------------------------------------------


def test_import_report_defaults():
    r = ImportReport()
    assert r.flows_upserted == 0
    assert r.qdrant_points_deleted == 0
    assert r.deleted_flow_ids == []
    assert isinstance(r.as_dict(), dict)
