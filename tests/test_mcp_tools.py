"""Smoke test for the MCP tool list after the flow demolition (AC-9).

Verifies:
- `kloc_explain_flow` and `kloc_flow_diagram` are NOT in the tool list.
- `kloc_import_flows` IS in the tool list with the rewritten description.
- `kloc_flows` (added in the follow-up) supports list and detail modes.
"""

from pathlib import Path

import pytest

from src.db.connection import Neo4jConnection
from src.config import Neo4jConfig
from src.db.flow_importer import (
    clear_flows,
    import_flow_edges,
    import_flow_nodes,
    load_symfony_kloc,
    parse_flows,
)
from src.server.mcp import MCPServer

from .conftest import requires_neo4j


REMOVED_TOOLS = ["kloc_explain_flow", "kloc_flow_diagram"]
KEPT_TOOL = "kloc_import_flows"
NEW_IMPORT_FLOWS_DESCRIPTION = (
    "Import symfony-kloc.json flows into Neo4j as :Flow nodes "
    "with FLOW_ENTRY and FLOW_TRIGGERS edges. "
    "Replaces all existing flows on each call."
)
NEW_FLOWS_TOOL = "kloc_flows"
REFERENCE_FIXTURE = Path(
    "/Users/michal/dev/ai/kloc/kloc-reference-project-php/.kloc/symfony-kloc.json"
)
ORDER_CREATE_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"


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


def test_kloc_import_flows_has_new_description():
    """B3: tool description must match the rewritten string from plan §Phase 2 Task B3."""
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == KEPT_TOOL), None)
    assert tool is not None
    assert tool["description"] == NEW_IMPORT_FLOWS_DESCRIPTION


def test_mcp_list_tools_includes_kloc_flows():
    """Follow-up: kloc_flows must appear in the tool list with correct schema."""
    server = MCPServer(database="neo4j")
    tool = next((t for t in server.get_tools() if t["name"] == NEW_FLOWS_TOOL), None)
    assert tool is not None, "kloc_flows must be in tools/list"
    schema = tool["inputSchema"]
    assert schema["required"] == []
    assert "flow_id" in schema["properties"]
    assert "type" in schema["properties"]


@pytest.fixture(scope="module")
def loaded_flows():
    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    try:
        conn.verify_connectivity()
    except Exception:
        pytest.skip("Neo4j unavailable")
    if not REFERENCE_FIXTURE.is_file():
        pytest.skip(f"Reference fixture not found: {REFERENCE_FIXTURE}")
    data = load_symfony_kloc(REFERENCE_FIXTURE)
    nodes, edges = parse_flows(data)
    clear_flows(conn)
    import_flow_nodes(conn, nodes)
    import_flow_edges(conn, edges)
    yield conn
    conn.close()


@requires_neo4j
def test_mcp_kloc_flows_list(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_flows", {})
    finally:
        server.close()
    assert result["mode"] == "list"
    assert len(result["flows"]) == 9


@requires_neo4j
def test_mcp_kloc_flows_detail(loaded_flows):
    server = MCPServer(database="neo4j")
    try:
        result = server.call_tool("kloc_flows", {"flow_id": ORDER_CREATE_FLOW_ID})
    finally:
        server.close()
    assert result["mode"] == "detail"
    assert result["flow"]["flow_id"] == ORDER_CREATE_FLOW_ID
    assert len(result["flow"]["triggers_out"]) == 2
