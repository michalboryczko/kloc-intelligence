"""Integration tests for the `flows` CLI command and supporting query module (v3).

Reference dataset (already loaded into Neo4j by the fixture):
- 10 flows in 4 types: 5 http, 1 message, 2 event, 2 cli
- OrderController::create  → dispatches_out: OrderCreatedEvent + OrderCreatedMessage
- OrderEventSubscriber::onOrderCreated → dispatches_in: OrderCreatedEvent (priority=0)
- OrderCreatedHandler::__invoke      → dispatches_in: OrderCreatedMessage
- PaymentController::verify → dispatches_out: AuditLogMessage + paypal.client http_client
"""

import json

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.db.queries.flows import find_flow, get_flow_detail, list_flows

from .conftest import REFERENCE_SYMFONY_KLOC_V3 as REFERENCE_FIXTURE
from .conftest import requires_neo4j

ORDER_CREATE_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"
ORDER_GET_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::get"
ORDER_HANDLER_FLOW_ID = "flow:message:App\\Ui\\Messenger\\Handler\\OrderCreatedHandler::__invoke"
ORDER_EVENT_SUB_FLOW_ID = (
    "flow:event:App\\Ui\\EventSubscriber\\OrderEventSubscriber::onOrderCreated[OrderCreatedEvent]"
)
PAYMENT_VERIFY_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\PaymentController::verify"


@pytest.fixture(scope="module")
def flows_loaded_connection(_loaded_database_conn):
    """Reuse the session-loaded Neo4j connection and (re)import the v3 flow fixture."""
    from src.db.flow_importer import run_import
    from src.db.schema import ensure_schema

    conn = _loaded_database_conn
    if not REFERENCE_FIXTURE.is_file():
        pytest.skip(f"v3 reference fixture not found: {REFERENCE_FIXTURE}")
    ensure_schema(conn)
    run_import(conn, str(REFERENCE_FIXTURE), None, None)
    return conn


# ---------------------------------------------------------------------------
# Direct query module tests (v3)
# ---------------------------------------------------------------------------


@requires_neo4j
def test_list_all_flows_returns_10(flows_loaded_connection):
    rows = list_flows(flows_loaded_connection)
    assert len(rows) == 10
    types = sorted(r["type"] for r in rows)
    assert types == [
        "cli",
        "cli",
        "event",
        "event",
        "http",
        "http",
        "http",
        "http",
        "http",
        "message",
    ]


@requires_neo4j
def test_list_flows_filtered_by_type(flows_loaded_connection):
    http_only = list_flows(flows_loaded_connection, type_filter=["http"])
    assert len(http_only) == 5
    assert all(r["type"] == "http" for r in http_only)
    routes = sorted(r["route"] for r in http_only)
    assert "/api/orders/{id}" in routes
    assert "/api/payments/{id}/verify" in routes


@requires_neo4j
def test_list_flows_unknown_type_returns_empty(flows_loaded_connection):
    assert list_flows(flows_loaded_connection, type_filter=["bogus"]) == []


@requires_neo4j
def test_find_flow_exact_match_returns_one(flows_loaded_connection):
    candidates = find_flow(flows_loaded_connection, ORDER_GET_FLOW_ID)
    assert len(candidates) == 1
    assert candidates[0]["flow_id"] == ORDER_GET_FLOW_ID


@requires_neo4j
def test_find_flow_partial_match_returns_candidates(flows_loaded_connection):
    candidates = find_flow(flows_loaded_connection, "OrderController")
    flow_ids = sorted(c["flow_id"] for c in candidates)
    assert ORDER_CREATE_FLOW_ID in flow_ids
    assert ORDER_GET_FLOW_ID in flow_ids
    assert len(flow_ids) >= 2


@requires_neo4j
def test_find_flow_unknown_returns_empty(flows_loaded_connection):
    assert find_flow(flows_loaded_connection, "definitely-does-not-exist-xyz") == []


@requires_neo4j
def test_flow_detail_uses_dispatches_keys_not_triggers(flows_loaded_connection):
    """Regression: v3 detail must not surface legacy triggers_in/triggers_out."""
    detail = get_flow_detail(flows_loaded_connection, ORDER_CREATE_FLOW_ID)
    assert detail is not None
    assert "triggers_in" not in detail
    assert "triggers_out" not in detail
    assert "dispatches_in" in detail
    assert "dispatches_out" in detail


@requires_neo4j
def test_flow_detail_dispatches_out_for_order_create(flows_loaded_connection):
    detail = get_flow_detail(flows_loaded_connection, ORDER_CREATE_FLOW_ID)
    assert detail is not None
    assert detail["flow_id"] == ORDER_CREATE_FLOW_ID
    assert detail["type"] == "http"

    out = detail["dispatches_out"]
    msg_fqns = [m["fqn"] for m in out["messages"]]
    evt_fqns = [e["fqn"] for e in out["events"]]
    assert "App\\Ui\\Messenger\\Message\\OrderCreatedMessage" in msg_fqns
    assert "App\\Event\\OrderCreatedEvent" in evt_fqns
    assert out["http_clients"] == []
    # OrderCreated event subscriber is the handler
    assert any(
        any("OrderEventSubscriber" in tf for tf in e.get("targets") or [{}][0].get("flow_id", ""))
        or any(
            t.get("flow_id", "").endswith("OrderEventSubscriber::onOrderCreated[OrderCreatedEvent]")
            for t in (e.get("targets") or [])
        )
        for e in out["events"]
    )

    assert detail["dispatches_in"]["messages"] == []
    assert detail["dispatches_in"]["events"] == []
    assert detail["entry"]["fqn"] == "App\\Ui\\Rest\\Controller\\OrderController::create"


@requires_neo4j
def test_flow_detail_dispatches_in_for_order_handler(flows_loaded_connection):
    detail = get_flow_detail(flows_loaded_connection, ORDER_HANDLER_FLOW_ID)
    assert detail is not None
    assert detail["dispatches_out"]["messages"] == []
    assert detail["dispatches_out"]["events"] == []
    assert detail["dispatches_out"]["http_clients"] == []
    in_msgs = detail["dispatches_in"]["messages"]
    assert len(in_msgs) == 1
    assert in_msgs[0]["fqn"] == "App\\Ui\\Messenger\\Message\\OrderCreatedMessage"
    assert ORDER_CREATE_FLOW_ID in in_msgs[0]["source_flow_ids"]


@requires_neo4j
def test_flow_detail_dispatches_in_for_order_event_subscriber(flows_loaded_connection):
    detail = get_flow_detail(flows_loaded_connection, ORDER_EVENT_SUB_FLOW_ID)
    assert detail is not None
    in_evts = detail["dispatches_in"]["events"]
    assert len(in_evts) == 1
    assert in_evts[0]["fqn"] == "App\\Event\\OrderCreatedEvent"
    assert in_evts[0]["priority"] == 0
    assert ORDER_CREATE_FLOW_ID in in_evts[0]["source_flow_ids"]


@requires_neo4j
def test_flow_detail_payment_verify_has_http_client_and_message(flows_loaded_connection):
    detail = get_flow_detail(flows_loaded_connection, PAYMENT_VERIFY_FLOW_ID)
    assert detail is not None
    out = detail["dispatches_out"]
    https = out["http_clients"]
    assert len(https) == 1
    assert https[0]["service_id"] == "paypal.client"
    assert https[0]["base_uri"] == "https://api.paypal.com"
    msgs = out["messages"]
    assert len(msgs) == 1
    assert msgs[0]["fqn"].endswith("AuditLogMessage")
    # AuditLogMessage has empty targets in the fixture
    assert msgs[0]["target_flow_ids"] == []


@requires_neo4j
def test_flow_detail_unknown_id_returns_none(flows_loaded_connection):
    assert get_flow_detail(flows_loaded_connection, "flow:http:Nope::nope") is None


# ---------------------------------------------------------------------------
# CLI integration tests (end-to-end through Typer)
# ---------------------------------------------------------------------------


@requires_neo4j
def test_cli_flows_list_json(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["flows", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "list"
    assert len(payload["flows"]) == 10


@requires_neo4j
def test_cli_flows_list_filter_type_http_json(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["flows", "--type", "http", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "list"
    assert len(payload["flows"]) == 5
    assert all(f["type"] == "http" for f in payload["flows"])


@requires_neo4j
def test_cli_flows_detail_exact_id_json_has_dispatches(flows_loaded_connection):
    """AC-34: detail JSON must contain dispatches_* (no triggers_*)."""
    runner = CliRunner()
    result = runner.invoke(app, ["flows", ORDER_CREATE_FLOW_ID, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "detail"
    flow = payload["flow"]
    assert flow["flow_id"] == ORDER_CREATE_FLOW_ID
    assert "dispatches_out" in flow
    assert "dispatches_in" in flow
    assert "triggers_in" not in flow
    assert "triggers_out" not in flow
    # OrderController::create emits OrderCreatedMessage + OrderCreatedEvent
    msgs = flow["dispatches_out"]["messages"]
    evts = flow["dispatches_out"]["events"]
    assert len(msgs) == 1
    assert len(evts) == 1


@requires_neo4j
def test_cli_flows_payment_verify_detail_includes_paypal(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["flows", PAYMENT_VERIFY_FLOW_ID, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    flow = payload["flow"]
    assert any(h["service_id"] == "paypal.client" for h in flow["dispatches_out"]["http_clients"])
    assert any(m["fqn"].endswith("AuditLogMessage") for m in flow["dispatches_out"]["messages"])


@requires_neo4j
def test_cli_flows_partial_match_returns_candidates_json(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["flows", "OrderController", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "candidates"
    assert len(payload["candidates"]) >= 2


@requires_neo4j
def test_cli_flows_unknown_query_returns_empty_candidates_json(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["flows", "no-such-flow-xyz", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "candidates"
    assert payload["candidates"] == []


@requires_neo4j
def test_cli_flows_invalid_type_exits_nonzero(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["flows", "--type", "bogus"])
    assert result.exit_code == 1
    assert "Unknown flow type" in result.output
