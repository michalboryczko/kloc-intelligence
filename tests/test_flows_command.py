"""Integration tests for the `flows` CLI command and supporting query module.

Reference dataset (already loaded into Neo4j by the fixture):
- 9 flows in 4 types: 4 http, 1 message, 2 event, 2 cli
- OrderController::create → triggers_out: OrderCreatedEvent + OrderCreatedMessage
- OrderEventSubscriber::onOrderCreated → triggers_in from OrderCreatedEvent
- OrderCreatedHandler::__invoke      → triggers_in from OrderCreatedMessage
"""

import json

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.config import Neo4jConfig
from src.db.connection import Neo4jConnection
from src.db.flow_importer import (
    clear_flows,
    import_flow_edges,
    import_flow_nodes,
    load_symfony_kloc,
    parse_flows,
)
from src.db.queries.flows import find_flow, get_flow_detail, list_flows

from .conftest import REFERENCE_SYMFONY_KLOC as REFERENCE_FIXTURE
from .conftest import requires_neo4j

ORDER_CREATE_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"
ORDER_GET_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::get"
ORDER_HANDLER_FLOW_ID = "flow:message:App\\Ui\\Messenger\\Handler\\OrderCreatedHandler::__invoke"


@pytest.fixture(scope="module")
def flows_loaded_connection(_loaded_database_conn):
    """Reuse the session-loaded Neo4j connection and (re)import the flow fixture once."""
    conn = _loaded_database_conn
    if not REFERENCE_FIXTURE.is_file():
        pytest.skip(f"Reference fixture not found: {REFERENCE_FIXTURE}")
    data = load_symfony_kloc(REFERENCE_FIXTURE)
    nodes, edges = parse_flows(data)
    clear_flows(conn)
    import_flow_nodes(conn, nodes)
    import_flow_edges(conn, edges)
    return conn


# ---------------------------------------------------------------------------
# Direct query module tests
# ---------------------------------------------------------------------------


@requires_neo4j
def test_list_all_flows_returns_9(flows_loaded_connection):
    rows = list_flows(flows_loaded_connection)
    assert len(rows) == 9
    types = sorted(r["type"] for r in rows)
    assert types == ["cli", "cli", "event", "event", "http", "http", "http", "http", "message"]


@requires_neo4j
def test_list_flows_filtered_by_type(flows_loaded_connection):
    http_only = list_flows(flows_loaded_connection, type_filter=["http"])
    assert len(http_only) == 4
    assert all(r["type"] == "http" for r in http_only)
    routes = sorted(r["route"] for r in http_only)
    assert "/api/orders/{id}" in routes


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
def test_flow_detail_includes_triggers_out(flows_loaded_connection):
    detail = get_flow_detail(flows_loaded_connection, ORDER_CREATE_FLOW_ID)
    assert detail is not None
    assert detail["flow_id"] == ORDER_CREATE_FLOW_ID
    assert detail["type"] == "http"
    via_classes = sorted(t["via"] for t in detail["triggers_out"])
    assert "App\\Event\\OrderCreatedEvent" in via_classes
    assert "App\\Ui\\Messenger\\Message\\OrderCreatedMessage" in via_classes
    assert detail["triggers_in"] == []
    assert detail["entry"]["fqn"] == "App\\Ui\\Rest\\Controller\\OrderController::create"


@requires_neo4j
def test_flow_detail_includes_triggers_in(flows_loaded_connection):
    detail = get_flow_detail(flows_loaded_connection, ORDER_HANDLER_FLOW_ID)
    assert detail is not None
    assert detail["triggers_out"] == []
    assert len(detail["triggers_in"]) == 1
    assert detail["triggers_in"][0]["via"] == "App\\Ui\\Messenger\\Message\\OrderCreatedMessage"
    assert detail["triggers_in"][0]["source_flow_id"] == ORDER_CREATE_FLOW_ID


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
    assert len(payload["flows"]) == 9


@requires_neo4j
def test_cli_flows_list_filter_type_http_json(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["flows", "--type", "http", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "list"
    assert len(payload["flows"]) == 4
    assert all(f["type"] == "http" for f in payload["flows"])


@requires_neo4j
def test_cli_flows_detail_exact_id_json(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["flows", ORDER_CREATE_FLOW_ID, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "detail"
    assert payload["flow"]["flow_id"] == ORDER_CREATE_FLOW_ID
    assert len(payload["flow"]["triggers_out"]) == 2


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
