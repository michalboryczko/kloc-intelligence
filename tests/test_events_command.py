"""Integration tests for the `events` CLI command and supporting queries.

Reference v3 fixture (already loaded into Neo4j by the fixture):
- 2 :Event nodes:
  - `event:App\\Event\\OrderCreatedEvent` — 1 source, 1 target (priority=0)
  - `event:App\\Event\\ReportGeneratedEvent` — 1 source, 1 target (priority=0)
"""

import json

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.db.queries.flows import (
    find_event,
    get_event_detail,
    list_events,
)

from .conftest import REFERENCE_SYMFONY_KLOC_V3 as REFERENCE_FIXTURE
from .conftest import requires_neo4j

ORDER_CREATED_EVENT_ID = "event:App\\Event\\OrderCreatedEvent"
REPORT_GENERATED_EVENT_ID = "event:App\\Event\\ReportGeneratedEvent"
ORDER_CREATE_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"
ORDER_EVENT_SUB_FLOW_ID = (
    "flow:event:App\\Ui\\EventSubscriber\\OrderEventSubscriber::onOrderCreated[OrderCreatedEvent]"
)


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
def test_list_events_returns_two(flows_loaded_connection):
    rows = list_events(flows_loaded_connection)
    assert len(rows) == 2
    ids = sorted(r["id"] for r in rows)
    assert ORDER_CREATED_EVENT_ID in ids
    assert REPORT_GENERATED_EVENT_ID in ids
    for r in rows:
        assert set(r.keys()) >= {"id", "fqn", "sources_count", "targets_count"}


@requires_neo4j
def test_list_events_counts(flows_loaded_connection):
    rows = list_events(flows_loaded_connection)
    for r in rows:
        assert r["sources_count"] == 1
        assert r["targets_count"] == 1


@requires_neo4j
def test_find_event_exact(flows_loaded_connection):
    rows = find_event(flows_loaded_connection, ORDER_CREATED_EVENT_ID)
    assert len(rows) == 1
    assert rows[0]["id"] == ORDER_CREATED_EVENT_ID


@requires_neo4j
def test_find_event_partial(flows_loaded_connection):
    rows = find_event(flows_loaded_connection, "ReportGenerated")
    ids = [r["id"] for r in rows]
    assert REPORT_GENERATED_EVENT_ID in ids


@requires_neo4j
def test_find_event_unknown_returns_empty(flows_loaded_connection):
    assert find_event(flows_loaded_connection, "no-such-event-xyz") == []


@requires_neo4j
def test_get_event_detail_order_created(flows_loaded_connection):
    detail = get_event_detail(flows_loaded_connection, ORDER_CREATED_EVENT_ID)
    assert detail is not None
    assert detail["id"] == ORDER_CREATED_EVENT_ID
    assert detail["fqn"] == "App\\Event\\OrderCreatedEvent"
    assert len(detail["sources"]) == 1
    assert detail["sources"][0]["flow_id"] == ORDER_CREATE_FLOW_ID
    assert len(detail["targets"]) == 1
    target = detail["targets"][0]
    assert target["flow_id"] == ORDER_EVENT_SUB_FLOW_ID
    assert target["priority"] == 0


@requires_neo4j
def test_get_event_detail_unknown_returns_none(flows_loaded_connection):
    assert get_event_detail(flows_loaded_connection, "event:nope") is None


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


@requires_neo4j
def test_cli_events_list_json(flows_loaded_connection):
    """AC-30: kloc events --json lists 2 events."""
    runner = CliRunner()
    result = runner.invoke(app, ["events", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "list"
    assert len(payload["events"]) == 2


@requires_neo4j
def test_cli_events_detail_order_created_json(flows_loaded_connection):
    """AC-31: detail shows priority=0, OF_TYPE class."""
    runner = CliRunner()
    result = runner.invoke(app, ["events", ORDER_CREATED_EVENT_ID, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "detail"
    evt = payload["event"]
    assert evt["id"] == ORDER_CREATED_EVENT_ID
    assert len(evt["targets"]) == 1
    assert evt["targets"][0]["priority"] == 0
    # of_type_class_fqn is present (None or string) — schema check
    assert "of_type_class_fqn" in evt


@requires_neo4j
def test_cli_events_unknown_query_returns_empty_candidates(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["events", "no-such-event-xyz", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "candidates"
    assert payload["candidates"] == []
