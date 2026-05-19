"""Streamable HTTP transport for the MCP server.

Implements the MCP 2025-03-26 Streamable HTTP transport:

- Single ``/mcp`` endpoint.
- ``POST`` accepts a JSON-RPC 2.0 request (or batch). Responses come back
  as ``application/json``. Notifications return ``202 Accepted`` with no body.
- ``GET`` is reserved for server-to-client streaming via SSE. We don't push
  unsolicited messages, so it returns ``405 Method Not Allowed``.
- ``DELETE`` is used to close a session; we don't keep server-side state,
  so it returns ``204 No Content`` unconditionally.

Tool dispatch lives on ``MCPServer.handle_jsonrpc`` — both this transport
and the stdio loop share that single entry point.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from .mcp import MCPServer


def create_app(server: MCPServer, path: str = "/mcp") -> Any:
    """Build a Starlette ASGI app that wraps the given MCPServer.

    Imports starlette lazily so importing this module without the ``http``
    extra installed still works (the CLI will surface the ImportError when
    the user actually starts the server).
    """
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def handle_mcp(request: Request) -> Response:
        if request.method == "POST":
            try:
                body = await request.json()
            except Exception as e:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {e}"},
                    },
                    status_code=400,
                )

            is_batch = isinstance(body, list)
            requests_list = body if is_batch else [body]

            responses: list[dict] = []
            for req in requests_list:
                if not isinstance(req, dict):
                    responses.append(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {
                                "code": -32600,
                                "message": "Invalid Request: not a JSON object",
                            },
                        }
                    )
                    continue
                resp = server.handle_jsonrpc(req)
                if resp is not None:
                    responses.append(resp)

            if not responses:
                # All inputs were notifications — spec says 202 with no body.
                return Response(status_code=202)

            if is_batch:
                return JSONResponse(responses)
            return JSONResponse(responses[0])

        if request.method == "GET":
            # No server-initiated streams from this implementation.
            return Response(status_code=405, headers={"Allow": "POST, DELETE"})

        if request.method == "DELETE":
            # We don't maintain server-side session state.
            return Response(status_code=204)

        return Response(status_code=405, headers={"Allow": "POST, GET, DELETE"})

    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "transport": "streamable-http"})

    routes = [
        Route(path, handle_mcp, methods=["GET", "POST", "DELETE"]),
        Route("/health", health, methods=["GET"]),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        try:
            yield
        finally:
            server.close()

    return Starlette(routes=routes, lifespan=lifespan)


def run_mcp_http_server(
    database: str = "neo4j",
    config_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    path: str = "/mcp",
) -> None:
    """Run the MCP server over Streamable HTTP using uvicorn.

    Args:
        database: Neo4j database name for single-project mode.
        config_path: Path to JSON config with project->database mapping.
        host: Bind address. Defaults to localhost.
        port: Bind port.
        path: HTTP path the MCP endpoint is mounted at.
    """
    try:
        import uvicorn
    except ImportError as e:
        raise ImportError(
            "HTTP transport requires the 'http' extra. Install with: uv sync --extra http"
        ) from e

    server = MCPServer(database=database, config_path=config_path)
    app = create_app(server, path=path)

    bound = f"http://{host}:{port}{path}"
    print(
        json.dumps({"event": "mcp_http_listening", "url": bound}),
        flush=True,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
