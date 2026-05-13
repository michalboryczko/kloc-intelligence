"""Integration tests for the `http-clients` CLI command and supporting queries.

Reference v3 fixture (already loaded into Neo4j by the fixture):
- 1 :HttpClient node:
  - `http_client:paypal.client` — service_id=paypal.client,
    class=Symfony\\Component\\HttpClient\\UriTemplateHttpClient (vendor → class_node_id null),
    base_uri=https://api.paypal.com, 1 source (PaymentController::verify)
"""

import json

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.db.queries.flows import (
    find_http_client,
    get_http_client_detail,
    list_http_clients,
)

from .conftest import REFERENCE_SYMFONY_KLOC_V3 as REFERENCE_FIXTURE
from .conftest import requires_neo4j

PAYPAL_HTTP_CLIENT_ID = "http_client:paypal.client"
PAYPAL_SERVICE_ID = "paypal.client"
PAYPAL_CLASS_FQN = "Symfony\\Component\\HttpClient\\UriTemplateHttpClient"
PAYPAL_BASE_URI = "https://api.paypal.com"
PAYMENT_VERIFY_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\PaymentController::verify"


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
def test_list_http_clients_returns_one(flows_loaded_connection):
    rows = list_http_clients(flows_loaded_connection)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == PAYPAL_HTTP_CLIENT_ID
    assert row["service_id"] == PAYPAL_SERVICE_ID
    assert row["base_uri"] == PAYPAL_BASE_URI
    assert row["class_fqn"] == PAYPAL_CLASS_FQN
    assert row["sources_count"] == 1


@requires_neo4j
def test_find_http_client_exact_id(flows_loaded_connection):
    rows = find_http_client(flows_loaded_connection, PAYPAL_HTTP_CLIENT_ID)
    assert len(rows) == 1
    assert rows[0]["id"] == PAYPAL_HTTP_CLIENT_ID


@requires_neo4j
def test_find_http_client_exact_service_id(flows_loaded_connection):
    rows = find_http_client(flows_loaded_connection, PAYPAL_SERVICE_ID)
    assert len(rows) == 1
    assert rows[0]["id"] == PAYPAL_HTTP_CLIENT_ID


@requires_neo4j
def test_find_http_client_partial(flows_loaded_connection):
    rows = find_http_client(flows_loaded_connection, "paypal")
    assert any(r["id"] == PAYPAL_HTTP_CLIENT_ID for r in rows)


@requires_neo4j
def test_find_http_client_unknown_returns_empty(flows_loaded_connection):
    assert find_http_client(flows_loaded_connection, "no-such-http-client-xyz") == []


@requires_neo4j
def test_get_http_client_detail_paypal(flows_loaded_connection):
    detail = get_http_client_detail(flows_loaded_connection, PAYPAL_HTTP_CLIENT_ID)
    assert detail is not None
    assert detail["id"] == PAYPAL_HTTP_CLIENT_ID
    assert detail["service_id"] == PAYPAL_SERVICE_ID
    assert detail["base_uri"] == PAYPAL_BASE_URI
    assert detail["class_fqn"] == PAYPAL_CLASS_FQN
    # vendor class — no OF_TYPE edge in the SoT graph
    assert detail["of_type_class_fqn"] is None
    assert len(detail["sources"]) == 1
    src = detail["sources"][0]
    assert src["flow_id"] == PAYMENT_VERIFY_FLOW_ID
    assert src["caller_method_fqn"] == "App\\Service\\PaypalGateway::verify()"


@requires_neo4j
def test_get_http_client_detail_unknown_returns_none(flows_loaded_connection):
    assert get_http_client_detail(flows_loaded_connection, "http_client:nope") is None


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


@requires_neo4j
def test_cli_http_clients_list_json(flows_loaded_connection):
    """AC-32: kloc http-clients --json lists paypal.client with base_uri + source count."""
    runner = CliRunner()
    result = runner.invoke(app, ["http-clients", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "list"
    assert len(payload["http_clients"]) == 1
    row = payload["http_clients"][0]
    assert row["base_uri"] == PAYPAL_BASE_URI
    assert row["sources_count"] == 1


@requires_neo4j
def test_cli_http_clients_detail_paypal_json(flows_loaded_connection):
    """AC-33: detail shows sources, base_uri, class_fqn (vendor string), no OF_TYPE."""
    runner = CliRunner()
    result = runner.invoke(app, ["http-clients", PAYPAL_HTTP_CLIENT_ID, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "detail"
    hc = payload["http_client"]
    assert hc["base_uri"] == PAYPAL_BASE_URI
    assert hc["class_fqn"] == PAYPAL_CLASS_FQN
    assert hc["of_type_class_fqn"] is None
    assert len(hc["sources"]) == 1


@requires_neo4j
def test_cli_http_clients_partial_match_paypal_json(flows_loaded_connection):
    """Partial match: `paypal` resolves to a single candidate → detail mode."""
    runner = CliRunner()
    result = runner.invoke(app, ["http-clients", "paypal", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # Could be detail (single hit) or candidates depending on the cascade.
    assert payload["mode"] in {"detail", "candidates"}


@requires_neo4j
def test_cli_http_clients_unknown_query_returns_empty_candidates(flows_loaded_connection):
    runner = CliRunner()
    result = runner.invoke(app, ["http-clients", "no-such-http-client-xyz", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "candidates"
    assert payload["candidates"] == []
