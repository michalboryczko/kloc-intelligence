"""Integration tests for the `messages` CLI command and supporting queries.

Reference v3 fixture (already loaded into Neo4j by the fixture):
- 2 :Message nodes:
  - `message:App\\Ui\\Messenger\\Message\\AuditLogMessage` — 1 source, 0 targets, transports=[]
  - `message:App\\Ui\\Messenger\\Message\\OrderCreatedMessage` — 1 source, 1 target, transports=["sync"]
"""

import json

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.db.queries.flows import (
    find_message,
    get_message_detail,
    list_messages,
)

from .conftest import REFERENCE_SYMFONY_KLOC_V3 as REFERENCE_FIXTURE
from .conftest import requires_neo4j

AUDIT_LOG_MESSAGE_ID = "message:App\\Ui\\Messenger\\Message\\AuditLogMessage"
ORDER_CREATED_MESSAGE_ID = "message:App\\Ui\\Messenger\\Message\\OrderCreatedMessage"
ORDER_CREATE_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"
PAYMENT_VERIFY_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\PaymentController::verify"
ORDER_HANDLER_FLOW_ID = "flow:message:App\\Ui\\Messenger\\Handler\\OrderCreatedHandler::__invoke"


@pytest.fixture(scope="module")
def flows_loaded_connection(_loaded_database_conn):
    from src.db.flow_importer import run_import
    from src.db.schema import ensure_schema

    conn = _loaded_database_conn
    if not REFERENCE_FIXTURE.is_file():
        pytest.skip(f"v3 reference fixture not found: {REFERENCE_FIXTURE}")
    ensure_schema(conn)
    run_import(conn, str(REFERENCE_FIXTURE), None, None)
    return conn


# ---------------------------------------------------------------------------
# Direct query tests
# ---------------------------------------------------------------------------


@requires_neo4j
def test_list_messages_returns_two(flows_loaded_connection):
    rows = list_messages(flows_loaded_connection)
    assert len(rows) == 2
    ids = sorted(r["id"] for r in rows)
    assert AUDIT_LOG_MESSAGE_ID in ids
    assert ORDER_CREATED_MESSAGE_ID in ids
    # Row shape (Interface Contracts)
    for r in rows:
        assert set(r.keys()) >= {"id", "fqn", "transports", "sources_count", "targets_count"}


@requires_neo4j
def test_list_messages_counts_for_order_created(flows_loaded_connection):
    rows = list_messages(flows_loaded_connection)
    order_created = next(r for r in rows if r["id"] == ORDER_CREATED_MESSAGE_ID)
    assert order_created["sources_count"] == 1
    assert order_created["targets_count"] == 1
    assert order_created["transports"] == ["sync"]


@requires_neo4j
def test_list_messages_counts_for_audit_log(flows_loaded_connection):
    rows = list_messages(flows_loaded_connection)
    audit = next(r for r in rows if r["id"] == AUDIT_LOG_MESSAGE_ID)
    assert audit["sources_count"] == 1
    assert audit["targets_count"] == 0
    assert audit["transports"] == []


@requires_neo4j
def test_find_message_exact_id(flows_loaded_connection):
    rows = find_message(flows_loaded_connection, ORDER_CREATED_MESSAGE_ID)
    assert len(rows) == 1
    assert rows[0]["id"] == ORDER_CREATED_MESSAGE_ID


@requires_neo4j
def test_find_message_partial(flows_loaded_connection):
    rows = find_message(flows_loaded_connection, "OrderCreated")
    ids = sorted(r["id"] for r in rows)
    assert ORDER_CREATED_MESSAGE_ID in ids


@requires_neo4j
def test_find_message_unknown_returns_empty(flows_loaded_connection):
    assert find_message(flows_loaded_connection, "definitely-no-such-message") == []


@requires_neo4j
def test_get_message_detail_order_created(flows_loaded_connection):
    detail = get_message_detail(flows_loaded_connection, ORDER_CREATED_MESSAGE_ID)
    assert detail is not None
    assert detail["id"] == ORDER_CREATED_MESSAGE_ID
    assert detail["transports"] == ["sync"]
    assert len(detail["sources"]) == 1
    src = detail["sources"][0]
    assert src["flow_id"] == ORDER_CREATE_FLOW_ID
    assert src["caller_method_fqn"] == "App\\Service\\OrderService::createOrder()"
    assert src["call_node_id"]
    assert len(detail["targets"]) == 1
    assert detail["targets"][0]["flow_id"] == ORDER_HANDLER_FLOW_ID


@requires_neo4j
def test_get_message_detail_audit_log_has_empty_targets(flows_loaded_connection):
    detail = get_message_detail(flows_loaded_connection, AUDIT_LOG_MESSAGE_ID)
    assert detail is not None
    assert detail["transports"] == []
    assert len(detail["sources"]) == 1
    assert detail["sources"][0]["flow_id"] == PAYMENT_VERIFY_FLOW_ID
    assert detail["targets"] == []


@requires_neo4j
def test_get_message_detail_unknown_returns_none(flows_loaded_connection):
    assert get_message_detail(flows_loaded_connection, "message:nope") is None


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


@requires_neo4j
def test_cli_messages_list_json(flows_loaded_connection):
    """AC-28: kloc messages --json lists all 2 messages with shape."""
    runner = CliRunner()
    result = runner.invoke(app, ["messages", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "list"
    assert len(payload["messages"]) == 2
    for r in payload["messages"]:
        assert "id" in r
        assert "fqn" in r
        assert "sources_count" in r
        assert "targets_count" in r


@requires_neo4j
def test_cli_messages_detail_order_created_json(flows_loaded_connection):
    """AC-29: detail returns sources/targets/transports."""
    runner = CliRunner()
    result = runner.invoke(app, ["messages", ORDER_CREATED_MESSAGE_ID, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "detail"
    msg = payload["message"]
    assert msg["id"] == ORDER_CREATED_MESSAGE_ID
    assert msg["transports"] == ["sync"]
    assert len(msg["sources"]) == 1
    assert msg["sources"][0]["caller_method_fqn"] == "App\\Service\\OrderService::createOrder()"
    assert len(msg["targets"]) == 1


@requires_neo4j
def test_cli_messages_detail_audit_log_has_no_targets(flows_loaded_connection):
    """AC-29b: AuditLogMessage detail: transports=[], targets=[], sources len 1."""
    runner = CliRunner()
    result = runner.invoke(app, ["messages", AUDIT_LOG_MESSAGE_ID, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    msg = payload["message"]
    assert msg["transports"] == []
    assert msg["targets"] == []
    assert len(msg["sources"]) == 1


@requires_neo4j
def test_cli_messages_partial_match_candidates(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["messages", "OrderCreated", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # 1 match exactly via partial — OrderCreatedMessage; exact non-match falls through to partial.
    # Could be detail (single hit) or candidates (multiple). Both are valid.
    assert payload["mode"] in {"detail", "candidates"}


@requires_neo4j
def test_cli_messages_unknown_query_returns_empty_candidates(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["messages", "no-such-message-xyz", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "candidates"
    assert payload["candidates"] == []
