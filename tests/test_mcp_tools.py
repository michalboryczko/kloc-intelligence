"""Smoke test for the MCP tool list after the flow demolition (AC-9).

Verifies:
- `kloc_explain_flow` and `kloc_flow_diagram` are NOT in the tool list.
- `kloc_import_flows` IS in the tool list with the rewritten description.
"""

from src.server.mcp import MCPServer


REMOVED_TOOLS = ["kloc_explain_flow", "kloc_flow_diagram"]
KEPT_TOOL = "kloc_import_flows"
NEW_IMPORT_FLOWS_DESCRIPTION = (
    "Import symfony-kloc.json flows into Neo4j as :Flow nodes "
    "with FLOW_ENTRY and FLOW_TRIGGERS edges. "
    "Replaces all existing flows on each call."
)


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
