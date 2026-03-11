"""MCP (Model Context Protocol) server for kloc-intelligence.

Implements JSON-RPC 2.0 based MCP protocol over stdio for AI agent integration.
Connects to Neo4j for graph queries with lazy connection (on first query).

Usage:
    kloc-intelligence mcp-server --database neo4j
    kloc-intelligence mcp-server --config /path/to/config.json

Config file format:
    {
        "projects": {
            "my-app": "my_app_db",
            "payments": "payments_db"
        }
    }
"""

import json
import signal
import sys
from typing import Any, Optional


def _count_tree_nodes(entries: list) -> int:
    """Count total nodes in a tree structure."""
    total = 0
    for entry in entries:
        total += 1
        if hasattr(entry, "children") and entry.children:
            total += _count_tree_nodes(entry.children)
    return total


class MCPServer:
    """MCP server for kloc-intelligence with multi-project support via Neo4j databases."""

    def __init__(
        self,
        database: str = "neo4j",
        config_path: Optional[str] = None,
    ):
        """Initialize server with database name or config file.

        Args:
            database: Neo4j database name for single-project mode.
            config_path: Path to JSON config with project->database mapping.
        """
        self._projects: dict[str, str] = {}  # name -> database
        self._runners: dict[str, Any] = {}  # name -> QueryRunner (lazy)
        self._connections: dict[str, Any] = {}  # name -> Neo4jConnection (lazy)

        if config_path:
            self._load_config(config_path)
        else:
            self._projects["default"] = database

    def _load_config(self, config_path: str):
        """Load projects from config file."""
        with open(config_path) as f:
            config = json.load(f)

        projects = config.get("projects", {})
        if not projects:
            raise ValueError("Config file must contain at least one project")

        if isinstance(projects, dict):
            for name, db in projects.items():
                self._projects[name] = db
        elif isinstance(projects, list):
            for proj in projects:
                name = proj.get("name")
                db = proj.get("database", proj.get("db", "neo4j"))
                if not name:
                    raise ValueError("Each project must have a 'name' field")
                self._projects[name] = db

    def _get_runner(self, project: Optional[str] = None):
        """Get QueryRunner for a project (lazy-loaded).

        Args:
            project: Project name. If None, uses default (only if single project).
        """
        from ..config import Neo4jConfig
        from ..db.connection import Neo4jConnection
        from ..db.query_runner import QueryRunner

        if project is None:
            if len(self._projects) == 1:
                project = list(self._projects.keys())[0]
            else:
                available = list(self._projects.keys())
                raise ValueError(
                    f"Multiple projects configured. Specify 'project' parameter. "
                    f"Available: {available}"
                )

        if project not in self._projects:
            available = list(self._projects.keys())
            raise ValueError(
                f"Unknown project: {project}. Available: {available}"
            )

        if project not in self._runners:
            database = self._projects[project]
            base_config = Neo4jConfig.from_env()
            config = Neo4jConfig(
                uri=base_config.uri,
                username=base_config.username,
                password=base_config.password,
                database=database,
            )
            conn = Neo4jConnection(config)
            conn.verify_connectivity()
            self._connections[project] = conn
            self._runners[project] = QueryRunner(conn)

        return self._runners[project]

    def close(self):
        """Close all connections."""
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()
        self._runners.clear()

    def get_projects(self) -> list[dict]:
        """Return list of configured projects."""
        return [
            {"name": name, "database": db}
            for name, db in self._projects.items()
        ]

    def get_tools(self) -> list[dict]:
        """Return list of available MCP tools."""
        project_prop = {
            "type": "string",
            "description": "Project name (required if multiple projects configured)",
        }

        return [
            {
                "name": "kloc_resolve",
                "description": (
                    "Resolve a PHP symbol to its definition location. "
                    "Supports FQN, partial match, or method syntax."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to resolve",
                        },
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_usages",
                "description": "Find all usages of a symbol with depth expansion.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to find usages for",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default: 50)",
                            "default": 50,
                        },
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_deps",
                "description": "Find all dependencies of a symbol with depth expansion.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to find dependencies for",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default: 50)",
                            "default": 50,
                        },
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_context",
                "description": (
                    "Get bidirectional context: what uses it and what it uses. "
                    "With include_impl, shows implementations/overrides."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to get context for",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results per direction (default: 50)",
                            "default": 50,
                        },
                        "include_impl": {
                            "type": "boolean",
                            "description": "Include implementations/overrides",
                            "default": False,
                        },
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_owners",
                "description": "Show structural containment chain (Method -> Class -> File).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol to find ownership for",
                        },
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_inherit",
                "description": "Show inheritance tree for a class/interface.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Class to show inheritance for",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down"],
                            "description": "up=ancestors, down=descendants",
                            "default": "up",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default: 100)",
                            "default": 100,
                        },
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_overrides",
                "description": "Show override tree for a method.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Method to show overrides for",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down"],
                            "description": "up=overridden, down=overriding",
                            "default": "up",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "BFS depth (default: 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default: 100)",
                            "default": 100,
                        },
                        "project": project_prop,
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "kloc_import",
                "description": "Import a sot.json file into Neo4j.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sot_path": {
                            "type": "string",
                            "description": "Path to sot.json file",
                        },
                        "clear": {
                            "type": "boolean",
                            "description": "Clear database before import",
                            "default": True,
                        },
                        "project": project_prop,
                    },
                    "required": ["sot_path"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a tool by name."""
        handlers = {
            "kloc_resolve": self._handle_resolve,
            "kloc_usages": self._handle_usages,
            "kloc_deps": self._handle_deps,
            "kloc_context": self._handle_context,
            "kloc_owners": self._handle_owners,
            "kloc_inherit": self._handle_inherit,
            "kloc_overrides": self._handle_overrides,
            "kloc_import": self._handle_import,
        }
        handler = handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")
        return handler(arguments)

    def _resolve_symbol(self, symbol: str, project: Optional[str] = None):
        """Resolve symbol and return first candidate or raise error."""
        from ..db.queries.resolve import resolve_symbol

        runner = self._get_runner(project)
        candidates = resolve_symbol(runner, symbol)

        if not candidates:
            raise ValueError(f"Symbol not found: {symbol}")

        return candidates[0]

    def _handle_resolve(self, args: dict) -> dict:
        project = args.get("project")
        node = self._resolve_symbol(args["symbol"], project)
        result: dict = {
            "id": node.node_id,
            "kind": node.kind,
            "name": node.name,
            "fqn": node.fqn,
            "file": node.file,
            "line": node.start_line + 1 if node.start_line is not None else None,
        }
        if node.signature:
            result["signature"] = node.signature
        return result

    def _handle_usages(self, args: dict) -> dict:
        from ..orchestration.usages import run_usages

        project = args.get("project")
        runner = self._get_runner(project)
        result = run_usages(
            runner,
            args["symbol"],
            depth=args.get("depth", 1),
            limit=args.get("limit", 50),
        )
        return result.to_dict()

    def _handle_deps(self, args: dict) -> dict:
        from ..orchestration.deps import run_deps

        project = args.get("project")
        runner = self._get_runner(project)
        result = run_deps(
            runner,
            args["symbol"],
            depth=args.get("depth", 1),
            limit=args.get("limit", 50),
        )
        return result.to_dict()

    def _handle_context(self, args: dict) -> dict:
        from ..orchestration.context import execute_context
        from ..models.output import ContextOutput

        project = args.get("project")
        runner = self._get_runner(project)
        result = execute_context(
            runner,
            args["symbol"],
            depth=args.get("depth", 1),
            limit=args.get("limit", 50),
            include_impl=args.get("include_impl", False),
        )
        return ContextOutput.from_result(result).to_dict()

    def _handle_owners(self, args: dict) -> dict:
        from ..orchestration.simple import run_owners

        project = args.get("project")
        runner = self._get_runner(project)
        result = run_owners(runner, args["symbol"])
        return result.to_dict()

    def _handle_inherit(self, args: dict) -> dict:
        from ..orchestration.simple import run_inherit

        project = args.get("project")
        runner = self._get_runner(project)
        result = run_inherit(
            runner,
            args["symbol"],
            direction=args.get("direction", "up"),
            depth=args.get("depth", 1),
            limit=args.get("limit", 100),
        )
        return result.to_dict()

    def _handle_overrides(self, args: dict) -> dict:
        from ..orchestration.simple import run_overrides

        project = args.get("project")
        runner = self._get_runner(project)
        result = run_overrides(
            runner,
            args["symbol"],
            direction=args.get("direction", "up"),
            depth=args.get("depth", 1),
            limit=args.get("limit", 100),
        )
        return result.to_dict()

    def _handle_import(self, args: dict) -> dict:
        from ..db.importer import parse_sot, import_nodes, import_edges
        from ..db.schema import ensure_schema, drop_all

        project = args.get("project")
        runner = self._get_runner(project)
        conn = runner._connection  # access underlying connection

        sot_path = args["sot_path"]
        clear = args.get("clear", True)

        nodes, edges = parse_sot(sot_path)

        if clear:
            drop_all(conn)
            ensure_schema(conn)

        import_nodes(conn, nodes)
        import_edges(conn, edges)

        return {
            "status": "ok",
            "nodes": len(nodes),
            "edges": len(edges),
        }


def run_mcp_server(
    database: str = "neo4j",
    config_path: Optional[str] = None,
):
    """Run the MCP server using stdio with JSON-RPC 2.0 protocol.

    Args:
        database: Neo4j database name for single-project mode.
        config_path: Path to JSON config with project->database mapping.
    """
    server = MCPServer(database=database, config_path=config_path)

    def shutdown(signum, frame):
        server.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    def send_response(
        req_id: Any, result: Any = None, error: Any = None,
    ):
        response: dict = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            response["error"] = {"code": -32000, "message": str(error)}
        else:
            response["result"] = result
        print(json.dumps(response), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            send_response(None, error=f"Parse error: {e}")
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        try:
            if method == "initialize":
                send_response(req_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "kloc-intelligence",
                        "version": "0.1.0",
                    },
                })
            elif method == "notifications/initialized":
                pass  # No response needed for notifications
            elif method == "tools/list":
                send_response(req_id, {"tools": server.get_tools()})
            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                tool_result = server.call_tool(tool_name, arguments)
                send_response(req_id, {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(tool_result, indent=2),
                    }],
                })
            elif method == "ping":
                send_response(req_id, {})
            else:
                send_response(req_id, error=f"Method not found: {method}")
        except Exception as e:
            send_response(req_id, error=str(e))

    server.close()
