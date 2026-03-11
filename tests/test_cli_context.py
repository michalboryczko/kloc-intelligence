"""Tests for CLI context command and MCP server."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.models.node import NodeData
from src.models.results import (
    ContextEntry,
    ContextResult,
    DefinitionInfo,
)
from src.output.console import (
    print_context_tree,
    context_tree_to_dict,
    print_definition_section,
    _format_entry_name,
)
from src.server.mcp import MCPServer


# ========================================================================
# Helpers
# ========================================================================


def _make_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "target-1",
        "kind": "Class",
        "name": "Order",
        "fqn": "App\\Entity\\Order",
        "symbol": "scip-php . . App/Entity/Order#",
        "file": "src/Entity/Order.php",
        "start_line": 10,
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _make_method_node(**overrides) -> NodeData:
    defaults = {
        "node_id": "method-1",
        "kind": "Method",
        "name": "getId",
        "fqn": "App\\Entity\\Order::getId()",
        "symbol": "scip-php . . App/Entity/Order#getId().",
        "file": "src/Entity/Order.php",
        "start_line": 20,
        "signature": "getId(): int",
    }
    defaults.update(overrides)
    return NodeData(**defaults)


def _make_context_result(
    target=None, used_by=None, uses=None, definition=None,
) -> ContextResult:
    return ContextResult(
        target=target or _make_node(),
        max_depth=1,
        used_by=used_by or [],
        uses=uses or [],
        definition=definition,
    )


def _make_definition(**overrides) -> DefinitionInfo:
    defaults = {
        "fqn": "App\\Entity\\Order",
        "kind": "Class",
        "file": "src/Entity/Order.php",
        "line": 10,
    }
    defaults.update(overrides)
    return DefinitionInfo(**defaults)


# ========================================================================
# Context tree printer tests
# ========================================================================


class TestPrintContextTree:
    """Tests for the console context tree printer."""

    def test_empty_context(self, capsys):
        """Empty context prints target info and 'None' for both sections."""
        result = _make_context_result()
        print_context_tree(result)
        out = capsys.readouterr().out
        assert "Context for" in out
        assert "USED BY" in out
        assert "USES" in out
        assert "None" in out

    def test_context_with_used_by(self, capsys):
        """Context with USED BY entries renders the tree."""
        used_by = [
            ContextEntry(
                depth=1,
                node_id="caller-1",
                fqn="App\\Service\\OrderService::createOrder()",
                kind="Method",
                file="src/Service/OrderService.php",
                line=30,
            ),
        ]
        result = _make_context_result(used_by=used_by)
        print_context_tree(result)
        out = capsys.readouterr().out
        assert "USED BY" in out
        assert "OrderService" in out

    def test_context_with_uses(self, capsys):
        """Context with USES entries renders the tree."""
        uses = [
            ContextEntry(
                depth=1,
                node_id="dep-1",
                fqn="App\\Entity\\User",
                kind="Class",
                file="src/Entity/User.php",
                line=5,
            ),
        ]
        result = _make_context_result(uses=uses)
        print_context_tree(result)
        out = capsys.readouterr().out
        assert "USES" in out
        assert "User" in out

    def test_context_with_definition(self, capsys):
        """Context with definition prints the DEFINITION section."""
        defn = _make_definition(
            signature="createOrder(OrderInput $input): Order",
            kind="Method",
            fqn="App\\Service\\OrderService::createOrder()",
        )
        result = _make_context_result(
            target=_make_method_node(
                fqn="App\\Service\\OrderService::createOrder()",
                name="createOrder",
                signature="createOrder(OrderInput $input): Order",
            ),
            definition=defn,
        )
        print_context_tree(result)
        out = capsys.readouterr().out
        assert "DEFINITION" in out
        assert "createOrder" in out

    def test_context_with_crossed_from(self, capsys):
        """Entries with crossed_from show the boundary indicator."""
        used_by = [
            ContextEntry(
                depth=1,
                node_id="caller-1",
                fqn="App\\Controller::handle()",
                kind="Method",
                file="src/Controller.php",
                line=10,
                crossed_from="App\\Service\\OrderService::process()",
            ),
        ]
        result = _make_context_result(used_by=used_by)
        print_context_tree(result)
        out = capsys.readouterr().out
        assert "crosses into" in out


class TestFormatEntryName:
    """Tests for _format_entry_name helper."""

    def test_method_with_signature(self):
        """Methods with signature show class::signature."""
        entry = ContextEntry(
            depth=1,
            node_id="m1",
            fqn="App\\Service\\OrderService::createOrder()",
            kind="Method",
            signature="createOrder(OrderInput $input): Order",
        )
        name = _format_entry_name(entry)
        assert name == "App\\Service\\OrderService::createOrder(OrderInput $input): Order"

    def test_class_without_signature(self):
        """Classes without signature show FQN."""
        entry = ContextEntry(
            depth=1,
            node_id="c1",
            fqn="App\\Entity\\Order",
            kind="Class",
        )
        name = _format_entry_name(entry)
        assert name == "App\\Entity\\Order"


class TestContextTreeToDict:
    """Tests for context_tree_to_dict JSON conversion."""

    def test_empty_context_to_dict(self):
        """Empty context produces valid dict."""
        result = _make_context_result()
        d = context_tree_to_dict(result)
        assert "target" in d
        assert "usedBy" in d
        assert "uses" in d
        assert d["usedBy"] == []
        assert d["uses"] == []

    def test_context_to_dict_with_entries(self):
        """Context with entries produces dict with proper structure."""
        used_by = [
            ContextEntry(
                depth=1,
                node_id="caller-1",
                fqn="App\\Service\\OrderService::createOrder()",
                kind="Method",
                file="src/Service/OrderService.php",
                line=30,
            ),
        ]
        result = _make_context_result(used_by=used_by)
        d = context_tree_to_dict(result)
        assert len(d["usedBy"]) == 1
        assert d["usedBy"][0]["fqn"] == "App\\Service\\OrderService::createOrder()"
        assert d["usedBy"][0]["line"] == 31  # 0-based to 1-based

    def test_context_to_dict_with_definition(self):
        """Context with definition includes the definition key."""
        defn = _make_definition(signature="getId(): int")
        result = _make_context_result(definition=defn)
        d = context_tree_to_dict(result)
        assert "definition" in d
        assert d["definition"]["fqn"] == "App\\Entity\\Order"


class TestPrintDefinitionSection:
    """Tests for the definition section printer."""

    def test_no_definition(self, capsys):
        """No definition does not print anything."""
        result = _make_context_result(definition=None)
        print_definition_section(result)
        out = capsys.readouterr().out
        assert out == ""

    def test_definition_with_signature(self, capsys):
        """Definition with signature shows it."""
        defn = _make_definition(
            signature="createOrder(OrderInput $input): Order",
            kind="Method",
        )
        result = _make_context_result(definition=defn)
        print_definition_section(result)
        out = capsys.readouterr().out
        assert "DEFINITION" in out
        assert "createOrder" in out

    def test_definition_with_arguments(self, capsys):
        """Definition with arguments shows them."""
        defn = _make_definition(
            signature="createOrder(OrderInput $input): Order",
            kind="Method",
            arguments=[
                {"name": "$input", "type": "OrderInput"},
            ],
        )
        result = _make_context_result(definition=defn)
        print_definition_section(result)
        out = capsys.readouterr().out
        assert "Arguments" in out
        assert "$input" in out


# ========================================================================
# MCP server tests
# ========================================================================


class TestMCPServerInit:
    """Tests for MCPServer initialization."""

    def test_default_project(self):
        """Single database creates default project."""
        server = MCPServer(database="test_db")
        assert server._projects == {"default": "test_db"}

    def test_config_file_dict(self, tmp_path):
        """Config with dict format loads projects."""
        config = {"projects": {"app": "app_db", "payments": "pay_db"}}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))
        server = MCPServer(config_path=str(config_file))
        assert server._projects == {"app": "app_db", "payments": "pay_db"}

    def test_config_file_list(self, tmp_path):
        """Config with list format loads projects."""
        config = {
            "projects": [
                {"name": "app", "database": "app_db"},
                {"name": "payments", "database": "pay_db"},
            ]
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))
        server = MCPServer(config_path=str(config_file))
        assert server._projects == {"app": "app_db", "payments": "pay_db"}

    def test_empty_config_raises(self, tmp_path):
        """Empty config raises ValueError."""
        config = {"projects": {}}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))
        with pytest.raises(ValueError, match="at least one project"):
            MCPServer(config_path=str(config_file))


class TestMCPServerTools:
    """Tests for MCP server tool listing."""

    def test_get_tools_returns_8(self):
        """get_tools returns 8 tools."""
        server = MCPServer(database="neo4j")
        tools = server.get_tools()
        assert len(tools) == 8

    def test_tool_names(self):
        """All expected tool names are present."""
        server = MCPServer(database="neo4j")
        tools = server.get_tools()
        names = {t["name"] for t in tools}
        expected = {
            "kloc_resolve",
            "kloc_usages",
            "kloc_deps",
            "kloc_context",
            "kloc_owners",
            "kloc_inherit",
            "kloc_overrides",
            "kloc_import",
        }
        assert names == expected

    def test_tool_has_input_schema(self):
        """Each tool has an inputSchema with properties and required."""
        server = MCPServer(database="neo4j")
        tools = server.get_tools()
        for tool in tools:
            assert "inputSchema" in tool
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema

    def test_resolve_tool_requires_symbol(self):
        """The resolve tool requires 'symbol' parameter."""
        server = MCPServer(database="neo4j")
        tools = server.get_tools()
        resolve_tool = next(t for t in tools if t["name"] == "kloc_resolve")
        assert "symbol" in resolve_tool["inputSchema"]["required"]


class TestMCPServerCallTool:
    """Tests for MCPServer.call_tool dispatch."""

    def test_unknown_tool_raises(self):
        """Calling unknown tool raises ValueError."""
        server = MCPServer(database="neo4j")
        with pytest.raises(ValueError, match="Unknown tool"):
            server.call_tool("nonexistent", {})

    @patch("src.server.mcp.MCPServer._get_runner")
    def test_resolve_dispatches(self, mock_get_runner):
        """Resolve dispatches to _handle_resolve."""
        mock_runner = MagicMock()
        mock_get_runner.return_value = mock_runner

        node = _make_node()
        with patch("src.server.mcp.MCPServer._resolve_symbol", return_value=node):
            server = MCPServer(database="neo4j")
            result = server.call_tool("kloc_resolve", {"symbol": "Order"})
            assert result["fqn"] == "App\\Entity\\Order"
            assert result["kind"] == "Class"

    @patch("src.server.mcp.MCPServer._get_runner")
    def test_resolve_returns_line_1_based(self, mock_get_runner):
        """Resolve converts 0-based line to 1-based."""
        mock_runner = MagicMock()
        mock_get_runner.return_value = mock_runner

        node = _make_node(start_line=10)
        with patch("src.server.mcp.MCPServer._resolve_symbol", return_value=node):
            server = MCPServer(database="neo4j")
            result = server.call_tool("kloc_resolve", {"symbol": "Order"})
            assert result["line"] == 11  # 0-based 10 -> 1-based 11


class TestMCPServerProjects:
    """Tests for multi-project support."""

    def test_get_projects(self):
        """get_projects returns list of configured projects."""
        server = MCPServer(database="neo4j")
        projects = server.get_projects()
        assert len(projects) == 1
        assert projects[0]["name"] == "default"
        assert projects[0]["database"] == "neo4j"

    def test_multi_project_requires_project_param(self, tmp_path):
        """Multi-project server requires project param for get_runner."""
        config = {"projects": {"app": "app_db", "pay": "pay_db"}}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))
        server = MCPServer(config_path=str(config_file))
        with pytest.raises(ValueError, match="Multiple projects"):
            server._get_runner()

    def test_unknown_project_raises(self):
        """Unknown project raises ValueError."""
        server = MCPServer(database="neo4j")
        with pytest.raises(ValueError, match="Unknown project"):
            server._get_runner("nonexistent")
