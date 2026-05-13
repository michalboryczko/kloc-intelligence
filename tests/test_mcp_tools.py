"""Smoke test for the MCP tool list after the v3 flow rewrite.

Verifies:
- `kloc_explain_flow` and `kloc_flow_diagram` are NOT in the tool list.
- `kloc_import_flows` IS in the tool list with the rewritten v3 description.
- `kloc_flows` (added in the follow-up) supports list and detail modes.
- The 6 new v3 tools are registered: `kloc_messages`, `kloc_message`,
  `kloc_events`, `kloc_event`, `kloc_http_clients`, `kloc_http_client`.
"""

import pytest

from src.config import Neo4jConfig
from src.db.connection import Neo4jConnection
from src.server.mcp import MCPServer

from .conftest import REFERENCE_SYMFONY_KLOC_V3 as REFERENCE_FIXTURE
from .conftest import requires_neo4j

REMOVED_TOOLS = ["kloc_explain_flow", "kloc_flow_diagram"]
KEPT_TOOL = "kloc_import_flows"
NEW_IMPORT_FLOWS_DESCRIPTION = (
    "Import symfony-kloc.json (v3) flows, messages, events, and "
    "HTTP clients into Neo4j. Idempotent: preserves "
    ":Flow.explanation across re-imports; deletes orphan Qdrant "
    "points by flow_id filter."
)
NEW_FLOWS_TOOL = "kloc_flows"
NEW_V3_TOOLS = [
    "kloc_messages",
    "kloc_message",
    "kloc_events",
    "kloc_event",
    "kloc_http_clients",
    "kloc_http_client",
]
ORDER_CREATE_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"
PAYMENT_VERIFY_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\PaymentController::verify"
ORDER_CREATED_MESSAGE_ID = "message:App\\Ui\\Messenger\\Message\\OrderCreatedMessage"
AUDIT_LOG_MESSAGE_ID = "message:App\\Ui\\Messenger\\Message\\AuditLogMessage"
ORDER_CREATED_EVENT_ID = "event:App\\Event\\OrderCreatedEvent"
PAYPAL_HTTP_CLIENT_ID = "http_client:paypal.client"


def _get_tool_names() -> set[str]:
    server = MCPServer(database="neo4j")
    return {t["name"] for t in server.get_tools()}


def test_mcp_list_tools_excludes_removed_flow_tools():
    """AC-9: kloc_explain_flow and kloc_flow_diagram must not appear in list_tools()."""
    names = _get_tool_names()
    for tool in REMOVED_TOOLS:
        assert tool not in names, f"Removed MCP tool {tool!r} still in tool list"


def test_mcp_list_tools_keeps_kloc_import_flows():
    """Regression: kloc_import_flows must still be present."""
    assert KEPT_TOOL in _get_tool_names()


def test_kloc_import_flows_has_v3_description():
    """v3: tool description must match the rewritten string."""
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == KEPT_TOOL), None)
    assert tool is not None
    assert tool["description"] == NEW_IMPORT_FLOWS_DESCRIPTION


def test_mcp_list_tools_includes_kloc_flows():
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == NEW_FLOWS_TOOL), None)
    assert tool is not None, "kloc_flows must be in tools/list"
    schema = tool["inputSchema"]
    assert schema["required"] == []
    assert "flow_id" in schema["properties"]
    assert "type" in schema["properties"]
    # v3 description must mention dispatches_*
    assert "dispatches_out" in tool["description"]


def test_mcp_list_tools_includes_kloc_enrich_flows():
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == "kloc_enrich_flows"), None)
    assert tool is not None, "kloc_enrich_flows must be in tools/list"
    assert "force" in tool["inputSchema"]["properties"]


def test_mcp_lists_new_v3_tools():
    """AC-35: 6 new v3 MCP tools must all be registered."""
    names = _get_tool_names()
    for tool in NEW_V3_TOOLS:
        assert tool in names, f"v3 MCP tool {tool!r} missing from tools/list"


def test_mcp_kloc_message_requires_id():
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == "kloc_message"), None)
    assert tool is not None
    assert tool["inputSchema"]["required"] == ["id"]


def test_mcp_kloc_event_requires_id():
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == "kloc_event"), None)
    assert tool is not None
    assert tool["inputSchema"]["required"] == ["id"]


def test_mcp_kloc_http_client_requires_id():
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == "kloc_http_client"), None)
    assert tool is not None
    assert tool["inputSchema"]["required"] == ["id"]


def test_mcp_kloc_messages_no_required():
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == "kloc_messages"), None)
    assert tool is not None
    assert tool["inputSchema"]["required"] == []


@pytest.fixture(scope="module")
def loaded_flows():
    from src.db.flow_importer import run_import
    from src.db.schema import ensure_schema

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    try:
        conn.verify_connectivity()
    except Exception:
        pytest.skip("Neo4j unavailable")
    if not REFERENCE_FIXTURE.is_file():
        pytest.skip(f"v3 reference fixture not found: {REFERENCE_FIXTURE}")
    ensure_schema(conn)
    run_import(conn, str(REFERENCE_FIXTURE), None, None)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Round-trip tool tests
# ---------------------------------------------------------------------------


@requires_neo4j
def test_mcp_kloc_flows_list(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_flows", {})
    finally:
        server.close()
    assert result["mode"] == "list"
    assert len(result["flows"]) == 10


@requires_neo4j
def test_mcp_kloc_flows_detail_has_dispatches(loaded_flows):
    """AC-37: kloc_flows detail returns v3 shape with dispatches_*, no triggers_*."""
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_flows", {"flow_id": PAYMENT_VERIFY_FLOW_ID})
    finally:
        server.close()
    assert result["mode"] == "detail"
    flow = result["flow"]
    assert flow["flow_id"] == PAYMENT_VERIFY_FLOW_ID
    assert "dispatches_out" in flow
    assert "dispatches_in" in flow
    assert "triggers_in" not in flow
    assert "triggers_out" not in flow
    assert any(m["id"].endswith("AuditLogMessage") for m in flow["dispatches_out"]["messages"])
    assert any(h["service_id"] == "paypal.client" for h in flow["dispatches_out"]["http_clients"])
    assert flow["dispatches_in"]["messages"] == []
    assert flow["dispatches_in"]["events"] == []


@requires_neo4j
def test_mcp_kloc_messages_list(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_messages", {})
    finally:
        server.close()
    assert result["mode"] == "list"
    assert len(result["messages"]) == 2
    ids = sorted(m["id"] for m in result["messages"])
    assert AUDIT_LOG_MESSAGE_ID in ids
    assert ORDER_CREATED_MESSAGE_ID in ids


@requires_neo4j
def test_mcp_kloc_message_detail(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_message", {"id": ORDER_CREATED_MESSAGE_ID})
    finally:
        server.close()
    assert result["mode"] == "detail"
    msg = result["message"]
    assert msg["id"] == ORDER_CREATED_MESSAGE_ID
    assert msg["transports"] == ["sync"]
    assert len(msg["sources"]) == 1
    assert len(msg["targets"]) == 1


@requires_neo4j
def test_mcp_kloc_events_list(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_events", {})
    finally:
        server.close()
    assert result["mode"] == "list"
    assert len(result["events"]) == 2


@requires_neo4j
def test_mcp_kloc_event_detail(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_event", {"id": ORDER_CREATED_EVENT_ID})
    finally:
        server.close()
    assert result["mode"] == "detail"
    evt = result["event"]
    assert evt["id"] == ORDER_CREATED_EVENT_ID
    assert len(evt["targets"]) == 1
    assert evt["targets"][0]["priority"] == 0


@requires_neo4j
def test_mcp_kloc_http_clients_list(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_http_clients", {})
    finally:
        server.close()
    assert result["mode"] == "list"
    assert len(result["http_clients"]) == 1
    assert result["http_clients"][0]["service_id"] == "paypal.client"


@requires_neo4j
def test_mcp_kloc_http_client_detail(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_http_client", {"id": PAYPAL_HTTP_CLIENT_ID})
    finally:
        server.close()
    assert result["mode"] == "detail"
    hc = result["http_client"]
    assert hc["id"] == PAYPAL_HTTP_CLIENT_ID
    assert hc["service_id"] == "paypal.client"
    assert hc["base_uri"] == "https://api.paypal.com"
    # Vendor class — no OF_TYPE edge per spec AC-24
    assert hc["of_type_class_fqn"] is None


# ---------------------------------------------------------------------------
# Single-source-of-truth: MCP --json output structurally equals CLI --json
# ---------------------------------------------------------------------------


@requires_neo4j
def test_mcp_kloc_messages_matches_cli(loaded_flows):
    """AC-36: MCP returns the same dict the CLI emits in --json mode."""
    import json

    from typer.testing import CliRunner

    from src.cli import app

    server = MCPServer(database="neo4j")
    try:
        mcp_out = server.call_tool("kloc_messages", {})
    finally:
        server.close()
    cli_out = json.loads(CliRunner().invoke(app, ["messages", "--json"]).output)
    assert mcp_out == cli_out


@requires_neo4j
def test_mcp_kloc_events_matches_cli(loaded_flows):
    import json

    from typer.testing import CliRunner

    from src.cli import app

    server = MCPServer(database="neo4j")
    try:
        mcp_out = server.call_tool("kloc_events", {})
    finally:
        server.close()
    cli_out = json.loads(CliRunner().invoke(app, ["events", "--json"]).output)
    assert mcp_out == cli_out


@requires_neo4j
def test_mcp_kloc_http_clients_matches_cli(loaded_flows):
    import json

    from typer.testing import CliRunner

    from src.cli import app

    server = MCPServer(database="neo4j")
    try:
        mcp_out = server.call_tool("kloc_http_clients", {})
    finally:
        server.close()
    cli_out = json.loads(CliRunner().invoke(app, ["http-clients", "--json"]).output)
    assert mcp_out == cli_out


# Backwards-compat alias for QA reference (older test imports may use this name).
NEW_IMPORT_FLOWS_DESCRIPTION_V3 = NEW_IMPORT_FLOWS_DESCRIPTION
