"""kloc-intelligence CLI interface."""

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="kloc-intelligence", help="Graph-native code intelligence platform")
schema_app = typer.Typer(name="schema", help="Schema management commands")
app.add_typer(schema_app, name="schema")

console = Console()


@schema_app.command("ensure")
def schema_ensure():
    """Create all constraints and indexes."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.schema import ensure_schema

    config = Neo4jConfig.from_env()
    with Neo4jConnection(config) as conn:
        result = ensure_schema(conn)
        console.print(f"[green]Schema ensured:[/green] {result['constraints']} constraints, "
                       f"{result['indexes']} indexes")


@schema_app.command("reset")
def schema_reset():
    """Drop all data and recreate schema."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.schema import drop_all, ensure_schema

    config = Neo4jConfig.from_env()
    with Neo4jConnection(config) as conn:
        drop_all(conn)
        result = ensure_schema(conn)
        console.print(f"[green]Schema reset:[/green] {result['constraints']} constraints, "
                       f"{result['indexes']} indexes")


@schema_app.command("verify")
def schema_verify():
    """Verify schema state."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.schema import verify_schema, get_node_count, get_edge_count

    config = Neo4jConfig.from_env()
    with Neo4jConnection(config) as conn:
        result = verify_schema(conn)
        nodes = get_node_count(conn)
        edges = get_edge_count(conn)

        table = Table(title="Schema Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Constraints", str(result["constraints"]))
        table.add_row("Indexes", str(result["indexes"]))
        table.add_row("Nodes", str(nodes))
        table.add_row("Edges", str(edges))
        console.print(table)


@app.command("import")
def import_sot(
    sot_path: str = typer.Argument(..., help="Path to sot.json file"),
    clear: bool = typer.Option(True, help="Clear database before import"),
    validate: bool = typer.Option(True, help="Validate after import"),
):
    """Import a sot.json file into Neo4j."""
    import time
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.schema import ensure_schema, drop_all
    from .db.importer import parse_sot, import_nodes, import_edges, validate_import

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    conn.verify_connectivity()
    start = time.perf_counter()

    console.print(f"Parsing {sot_path}...")
    nodes, edges = parse_sot(sot_path)
    console.print(f"  Parsed {len(nodes):,} nodes, {len(edges):,} edges")

    if clear:
        console.print("Clearing database...")
        drop_all(conn)
        ensure_schema(conn)

    console.print("Importing nodes...")
    import_nodes(conn, nodes)
    console.print("Importing edges...")
    import_edges(conn, edges)

    if validate:
        report = validate_import(conn, len(nodes), len(edges))
        node_status = "OK" if report["node_match"] else "MISMATCH"
        edge_status = "OK" if report["edge_match"] else "MISMATCH"
        console.print(
            f"  Nodes: {report['node_count']:,} / {report['expected_nodes']:,}  {node_status}"
        )
        console.print(
            f"  Edges: {report['edge_count']:,} / {report['expected_edges']:,}  {edge_status}"
        )

    total = time.perf_counter() - start
    console.print(f"\nImport complete in {total:.1f}s")
    conn.close()


@app.command()
def resolve(
    query: str = typer.Argument(..., help="Symbol to resolve"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Resolve a symbol to its definition(s)."""
    import json as json_mod

    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .db.queries.resolve import resolve_symbol as do_resolve

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    runner = QueryRunner(conn)
    candidates = do_resolve(runner, query)

    if output_json:
        result = {
            "query": query,
            "candidates": [
                {
                    "id": n.node_id,
                    "kind": n.kind,
                    "name": n.name,
                    "fqn": n.fqn,
                    "file": n.file,
                    "line": n.start_line + 1 if n.start_line is not None else None,
                }
                for n in candidates
            ],
        }
        console.print(json_mod.dumps(result, indent=2))
    else:
        if not candidates:
            console.print(f"No matches for: {query}")
        else:
            table = Table(title=f"Resolve: {query}")
            table.add_column("Kind", style="cyan")
            table.add_column("FQN", style="green")
            table.add_column("File", style="yellow")
            table.add_column("Line")
            for n in candidates:
                table.add_row(
                    n.kind,
                    n.fqn,
                    n.file or "",
                    str(n.start_line + 1) if n.start_line is not None else "",
                )
            console.print(table)
    conn.close()


@app.command()
def usages(
    query: str = typer.Argument(..., help="Symbol to find usages of"),
    depth: int = typer.Option(1, "--depth", "-d", help="BFS depth for expansion"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum total results"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Find all usages of a symbol (incoming USES edges)."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .orchestration.usages import run_usages
    from .output.json_formatter import print_json as do_print_json
    from .output.console import print_usages_result

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    runner = QueryRunner(conn)

    try:
        result = run_usages(runner, query, depth=depth, limit=limit)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if output_json:
        do_print_json(result.to_dict())
    else:
        print_usages_result(result)

    conn.close()


@app.command()
def deps(
    query: str = typer.Argument(..., help="Symbol to find dependencies of"),
    depth: int = typer.Option(1, "--depth", "-d", help="BFS depth for expansion"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum total results"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Find all dependencies of a symbol (outgoing USES edges)."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .orchestration.deps import run_deps
    from .output.json_formatter import print_json as do_print_json
    from .output.console import print_deps_result

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    runner = QueryRunner(conn)

    try:
        result = run_deps(runner, query, depth=depth, limit=limit)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if output_json:
        do_print_json(result.to_dict())
    else:
        print_deps_result(result)

    conn.close()


@app.command()
def owners(
    query: str = typer.Argument(..., help="Symbol to find owners of"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show containment chain for a symbol (Method -> Class -> File)."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .orchestration.simple import run_owners
    from .output.json_formatter import print_json as do_print_json
    from .output.console import print_owners_result

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    runner = QueryRunner(conn)

    try:
        result = run_owners(runner, query)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if output_json:
        do_print_json(result.to_dict())
    else:
        print_owners_result(result)

    conn.close()


@app.command()
def inherit(
    query: str = typer.Argument(..., help="Symbol to find inheritance tree of"),
    direction: str = typer.Option("up", "--direction", "-D", help="Direction: up (ancestors) or down (descendants)"),
    depth: int = typer.Option(5, "--depth", "-d", help="Maximum BFS depth"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum total results"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show inheritance tree for a class/interface/trait/enum."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .orchestration.simple import run_inherit
    from .output.json_formatter import print_json as do_print_json
    from .output.console import print_inherit_result

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    runner = QueryRunner(conn)

    try:
        result = run_inherit(runner, query, direction=direction, depth=depth, limit=limit)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if output_json:
        do_print_json(result.to_dict())
    else:
        print_inherit_result(result)

    conn.close()


@app.command()
def context(
    symbol: str = typer.Argument(..., help="Symbol to get context for"),
    depth: int = typer.Option(1, "--depth", "-d", help="BFS depth for expansion"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum results per direction"),
    impl: bool = typer.Option(False, "--impl", "-i", help="Include implementations/overrides"),
    direct: bool = typer.Option(False, "--direct", help="Direct references only"),
    with_imports: bool = typer.Option(False, "--with-imports", help="Include PHP imports"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Get bidirectional context: what uses a symbol and what it uses."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .db.queries.resolve import resolve_symbol as do_resolve
    from .orchestration.context import execute_context
    from .output.json_formatter import print_json as do_print_json
    from .output.console import print_context_tree, context_tree_to_dict

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    runner = QueryRunner(conn)

    # Resolve symbol first
    candidates = do_resolve(runner, symbol)
    if not candidates:
        console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    try:
        result = execute_context(
            runner, symbol,
            depth=depth, limit=limit,
            include_impl=impl, direct_only=direct,
            with_imports=with_imports,
        )
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if output_json:
        do_print_json(context_tree_to_dict(result))
    else:
        print_context_tree(result)

    conn.close()


@app.command()
def overrides(
    query: str = typer.Argument(..., help="Method to find overrides for"),
    direction: str = typer.Option("up", "--direction", "-D", help="Direction: up (parent) or down (children)"),
    depth: int = typer.Option(5, "--depth", "-d", help="Maximum BFS depth"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum total results"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show override chain for a method."""
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .orchestration.simple import run_overrides
    from .output.json_formatter import print_json as do_print_json
    from .output.console import print_overrides_result

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    runner = QueryRunner(conn)

    try:
        result = run_overrides(runner, query, direction=direction, depth=depth, limit=limit)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if output_json:
        do_print_json(result.to_dict())
    else:
        print_overrides_result(result)

    conn.close()


@app.command("mcp-server")
def mcp_server(
    database: str = typer.Option("neo4j", "--database", "-db", help="Neo4j database name"),
    config: str = typer.Option(None, "--config", help="Path to config JSON with project->database mapping"),
):
    """Start MCP server for AI agent integration."""
    from .server.mcp import run_mcp_server

    run_mcp_server(database=database, config_path=config)


if __name__ == "__main__":
    app()
