"""Tests for the Streamable HTTP MCP transport.

Uses Starlette's TestClient against the ASGI app — no real network, no
Neo4j required for protocol-level checks. Tool-call dispatch is verified
by monkey-patching ``MCPServer.call_tool`` so we don't need a loaded DB.
"""

from __future__ import annotations

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient

from src.server.mcp import MCPServer
from src.server.mcp_http import create_app


def _client() -> TestClient:
    server = MCPServer(database="neo4j")
    app = create_app(server)
    return TestClient(app)


def test_health_endpoint() -> None:
    with _client() as c:
        resp = c.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "transport": "streamable-http"}


def test_initialize_returns_protocol_version() -> None:
    with _client() as c:
        resp = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert body["result"]["serverInfo"]["name"] == "kloc-intelligence"
    assert "protocolVersion" in body["result"]


def test_ping_roundtrip() -> None:
    with _client() as c:
        resp = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "ping-1", "method": "ping"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"jsonrpc": "2.0", "id": "ping-1", "result": {}}


def test_tools_list_exposes_v3_tools() -> None:
    with _client() as c:
        resp = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
    assert resp.status_code == 200
    tools = {t["name"] for t in resp.json()["result"]["tools"]}
    # Spot-check: stdio and HTTP must surface the same tool set.
    for expected in (
        "kloc_resolve",
        "kloc_context",
        "kloc_flows",
        "kloc_messages",
        "kloc_http_clients",
    ):
        assert expected in tools


def test_notification_returns_202_no_body() -> None:
    with _client() as c:
        # No "id" => notification.
        resp = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
    assert resp.status_code == 202
    assert resp.content == b""


def test_unknown_method_returns_error_object() -> None:
    with _client() as c:
        resp = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 99, "method": "tools/does_not_exist"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 99
    assert body["error"]["code"] == -32601
    assert "Method not found" in body["error"]["message"]


def test_invalid_json_returns_parse_error() -> None:
    with _client() as c:
        resp = c.post(
            "/mcp",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == -32700


def test_batch_request_returns_array() -> None:
    with _client() as c:
        resp = c.post(
            "/mcp",
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            ],
        )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert {r["id"] for r in body} == {1, 2}


def test_batch_of_notifications_returns_202() -> None:
    with _client() as c:
        resp = c.post(
            "/mcp",
            json=[
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ],
        )
    assert resp.status_code == 202


def test_get_method_is_rejected() -> None:
    with _client() as c:
        resp = c.get("/mcp")
    assert resp.status_code == 405
    assert "POST" in resp.headers.get("allow", "")


def test_delete_returns_204() -> None:
    with _client() as c:
        resp = c.delete("/mcp")
    assert resp.status_code == 204


def test_tools_call_dispatches_to_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """tools/call must hand off to MCPServer.call_tool and wrap the result
    in MCP content blocks."""
    captured: dict = {}

    def fake_call_tool(self: MCPServer, name: str, arguments: dict):
        captured["name"] = name
        captured["arguments"] = arguments
        return {"echo": arguments}

    monkeypatch.setattr(MCPServer, "call_tool", fake_call_tool, raising=True)

    server = MCPServer(database="neo4j")
    app = create_app(server)
    with TestClient(app) as c:
        resp = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "kloc_resolve", "arguments": {"symbol": "Foo"}},
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert captured == {"name": "kloc_resolve", "arguments": {"symbol": "Foo"}}
    content = body["result"]["content"]
    assert content[0]["type"] == "text"
    assert '"echo"' in content[0]["text"]
