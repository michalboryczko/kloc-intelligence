"""kloc-intelligence CLI interface."""

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

# Load .env file (doesn't override existing env vars like LLM_API_KEY from .zshrc)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.is_file():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:  # don't override shell env
                os.environ[key] = value

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


def _require_ai_deps():
    """Check that AI optional dependencies are installed."""
    try:
        import haystack  # noqa: F401
        import qdrant_client  # noqa: F401
    except ImportError:
        console.print("[red]AI features require additional dependencies.[/red]")
        console.print("Install with: uv sync --extra ai")
        raise typer.Exit(1)


def _setup_logging(debug: bool):
    """Configure logging for AI commands."""
    import logging
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=logging.WARNING,  # suppress third-party noise
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Only show debug/info for our AI modules
    for name in ("src.ai", "src.ai.enricher", "src.ai.source_reader", "src.ai.chunker", "src.ai.pipelines"):
        logging.getLogger(name).setLevel(level)


@app.command()
def explain(
    symbol: str = typer.Argument(..., help="Symbol to get explanation for"),
    project_root: str = typer.Option(None, "--project-root", "-r", help="Path to PHP project root"),
    force: bool = typer.Option(False, "--force", "-f", help="Regenerate even if exists"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
):
    """Show or generate human-language explanation for a class/method."""
    import json as json_mod

    _require_ai_deps()
    _setup_logging(debug)
    from .ai.config import AIConfig
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .db.queries.resolve import resolve_symbol as do_resolve
    from .ai.enricher import Enricher

    neo4j_config = Neo4jConfig.from_env()
    ai_config = AIConfig.from_env()
    if project_root:
        ai_config.project_root = project_root

    conn = Neo4jConnection(neo4j_config)
    runner = QueryRunner(conn)

    candidates = do_resolve(runner, symbol)
    if not candidates:
        console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    node = candidates[0]
    if node.kind not in ("Class", "Method"):
        console.print(f"[yellow]Explain is available for Class/Method nodes, got: {node.kind}[/yellow]")
        raise typer.Exit(1)

    # Check for existing explanation
    existing = runner.execute_single(
        "MATCH (n:Node {node_id: $nid}) RETURN n.explanation AS expl, n.explain_model AS model",
        nid=node.node_id,
    )
    has_existing = existing and existing["expl"] and not force

    if has_existing:
        explanation = existing["expl"]
        model = existing["model"] or ""
    else:
        errors = ai_config.validate()
        if errors:
            for e in errors:
                console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        enricher = Enricher(runner, ai_config)
        result = enricher.enrich_node(node.node_id, force=True)
        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
            raise typer.Exit(1)
        explanation = result["explanation"]
        model = ai_config.llm.model

    if output_json:
        out = {
            "node_id": node.node_id,
            "kind": node.kind,
            "fqn": node.fqn,
            "file": node.file,
            "line": node.start_line + 1 if node.start_line is not None else None,
            "explanation": explanation,
            "model": model,
        }
        console.print(json_mod.dumps(out, indent=2))
    else:
        line_str = f":{node.start_line + 1}" if node.start_line is not None else ""
        console.print(f"\n[bold]Explanation for {node.fqn}[/bold] ({node.kind})")
        console.print(f"[dim]defined at: {node.file or '<unknown>'}{line_str}[/dim]\n")
        console.print(explanation)
        console.print(f"\n[dim][{model}][/dim]")

    conn.close()


@app.command()
def search(
    query: str = typer.Argument(..., help="Natural language search query"),
    collection: str = typer.Option("both", "--collection", "-c", help="Search in: code, explain, both"),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum results"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Semantic search across code and explanations."""
    import json as json_mod

    _require_ai_deps()
    from .ai.config import AIConfig
    from .ai.pipelines import build_search_pipeline, run_search, search_both_collections

    ai_config = AIConfig.from_env()
    if not ai_config.embedding.api_key:
        console.print("[red]EMBEDDING_API_KEY is required for search[/red]")
        raise typer.Exit(1)

    if collection == "both":
        hits = search_both_collections(ai_config, query, limit=limit)
    else:
        col_name = "code_embeddings" if collection == "code" else "explain_embeddings"
        pipeline = build_search_pipeline(ai_config, col_name)
        hits = run_search(pipeline, query, top_k=limit)
        for h in hits:
            h["collection"] = col_name

    if output_json:
        console.print(json_mod.dumps({"query": query, "hits": hits}, indent=2))
    else:
        console.print(f"\n[bold]Search:[/bold] \"{query}\"\n")
        if not hits:
            console.print("[yellow]No results found.[/yellow]")
        else:
            table = Table()
            table.add_column("#", style="dim", width=4)
            table.add_column("Score", width=6)
            table.add_column("Kind", style="cyan", width=8)
            table.add_column("FQN", style="green")
            table.add_column("File", style="yellow")
            for i, hit in enumerate(hits, 1):
                table.add_row(
                    str(i),
                    f"{hit['score']:.2f}",
                    hit["kind"],
                    hit["fqn"],
                    hit.get("file") or "",
                )
            console.print(table)

    # No conn.close() needed — search doesn't use Neo4j directly


@app.command()
def enrich(
    project_root: str = typer.Option(None, "--project-root", "-r", help="Path to PHP project root"),
    kinds: str = typer.Option("Class,Method", "--kinds", "-k", help="Node kinds to enrich"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-enrich already processed nodes"),
    batch_size: int = typer.Option(10, "--batch-size", "-b", help="Nodes per batch"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
):
    """Batch generate explanations and embeddings for all class/method nodes."""
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

    _require_ai_deps()
    _setup_logging(debug)
    from .ai.config import AIConfig
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .ai.enricher import Enricher

    neo4j_config = Neo4jConfig.from_env()
    ai_config = AIConfig.from_env()
    if project_root:
        ai_config.project_root = project_root

    errors = ai_config.validate()
    if errors:
        for e in errors:
            console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    conn = Neo4jConnection(neo4j_config)
    runner = QueryRunner(conn)
    enricher = Enricher(runner, ai_config)

    kind_list = [k.strip() for k in kinds.split(",")]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching nodes...", total=0)

        def on_progress(p):
            progress.update(task, total=p.total, completed=p.processed + p.skipped + p.failed)
            progress.update(
                task,
                description=f"Enriching... ({p.processed} done, {p.skipped} skipped, {p.failed} failed)",
            )

        result = enricher.enrich_all(
            force=force, kinds=kind_list, batch_size=batch_size, callback=on_progress
        )

    console.print(f"\n[green]Enrichment complete:[/green]")
    console.print(f"  Processed: {result.processed}")
    console.print(f"  Skipped:   {result.skipped}")
    console.print(f"  Failed:    {result.failed}")
    if result.failed_nodes:
        console.print(f"\n[yellow]Failed nodes:[/yellow]")
        for fqn in result.failed_nodes:
            console.print(f"  - {fqn}")

    conn.close()


@app.command("enrich-status")
def enrich_status(
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show progress of enrichment (how many nodes enriched vs total)."""
    import json as json_mod

    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.query_runner import QueryRunner
    from .ai.enricher import Enricher

    neo4j_config = Neo4jConfig.from_env()
    conn = Neo4jConnection(neo4j_config)
    runner = QueryRunner(conn)

    # Enricher.get_status() only queries Neo4j, no AI deps needed
    from .ai.config import AIConfig
    ai_config = AIConfig()
    enricher = Enricher(runner, ai_config)
    status = enricher.get_status()

    if output_json:
        console.print(json_mod.dumps(status, indent=2))
    else:
        console.print("\n[bold]Enrichment Status[/bold]\n")
        table = Table()
        table.add_column("Kind", style="cyan")
        table.add_column("Total", justify="right")
        table.add_column("Enriched", justify="right", style="green")
        table.add_column("Pending", justify="right", style="yellow")
        for kind, stats in status["kinds"].items():
            table.add_row(
                kind,
                str(stats["total"]),
                str(stats["enriched"]),
                str(stats["pending"]),
            )
        console.print(table)

        total = status["total"]
        enriched = status["enriched"]
        pct = (enriched / total * 100) if total > 0 else 0
        console.print(f"\n  Overall: {enriched}/{total} ({pct:.1f}%)")

    conn.close()


@app.command("import-flows")
def import_flows(
    path: str = typer.Argument(..., help="Path to symfony-kloc.json"),
):
    """Import symfony-kloc.json flows into Neo4j as :Flow nodes with FLOW_ENTRY and FLOW_TRIGGERS edges.

    Replaces all existing flows on each call and drops the legacy flow_* Qdrant collections.
    """
    import os
    import time as time_mod
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.schema import ensure_schema
    from .db.flow_importer import load_symfony_kloc, parse_flows, import_flow_nodes, import_flow_edges, clear_flows

    stale_qdrant_collections = (
        "flow_business_embeddings",
        "flow_technical_embeddings",
        "flow_search_embeddings",
    )

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    conn.verify_connectivity()
    start = time_mod.perf_counter()

    ensure_schema(conn)

    console.print(f"Parsing {path}...")
    data = load_symfony_kloc(path)
    nodes, edges = parse_flows(data)
    entry_count = sum(1 for e in edges if e["type"] == "flow_entry")
    trigger_count = sum(1 for e in edges if e["type"] == "flow_triggers")
    console.print(
        f"  Parsed {len(nodes)} flow nodes, {entry_count} FLOW_ENTRY edges, "
        f"{trigger_count} FLOW_TRIGGERS edges"
    )

    try:
        from qdrant_client import QdrantClient
        qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
        qdrant_api_key = os.environ.get("QDRANT_API_KEY") or None
        qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        for name in stale_qdrant_collections:
            try:
                qdrant.delete_collection(name)
            except Exception:
                pass
        qdrant.close()
    except Exception as exc:
        console.print(f"[yellow]Skipping Qdrant flow_* cleanup: {exc}[/yellow]")

    console.print("Clearing existing flows...")
    clear_flows(conn)

    console.print("Importing flow nodes...")
    import_flow_nodes(conn, nodes)
    console.print("Importing flow edges...")
    import_flow_edges(conn, edges)

    total = time_mod.perf_counter() - start
    console.print(
        f"\n[green]Imported {len(nodes)} flows, {entry_count} FLOW_ENTRY edges, "
        f"{trigger_count} FLOW_TRIGGERS edges in {total:.1f}s[/green]"
    )
    conn.close()


@app.command()
def flows(
    query: str = typer.Argument(None, help="Optional flow_id, partial match, or entry FQN"),
    type_filter: str = typer.Option(None, "--type", "-t", help="Filter by type: http,message,event,cli (comma-separated)"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List or inspect application flows.

    No argument lists all flows (filter with --type).
    With an argument, returns full detail for an exact flow_id, or a candidate list
    when the query partially matches multiple flows.
    """
    import json as json_mod
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.queries.flows import (
        VALID_FLOW_TYPES,
        list_flows,
        find_flow,
        get_flow_detail,
    )

    config = Neo4jConfig.from_env()
    conn = Neo4jConnection(config)
    conn.verify_connectivity()

    types: list[str] | None = None
    if type_filter:
        types = [t.strip() for t in type_filter.split(",") if t.strip()]
        unknown = [t for t in types if t not in VALID_FLOW_TYPES]
        if unknown:
            console.print(
                f"[red]Unknown flow type(s): {', '.join(unknown)}. "
                f"Allowed: {', '.join(sorted(VALID_FLOW_TYPES))}.[/red]"
            )
            conn.close()
            raise typer.Exit(1)

    if query is None:
        result = {"mode": "list", "flows": list_flows(conn, type_filter=types)}
    else:
        candidates = find_flow(conn, query)
        if len(candidates) == 0:
            result = {"mode": "candidates", "candidates": []}
        elif len(candidates) == 1:
            detail = get_flow_detail(conn, candidates[0]["flow_id"])
            result = {"mode": "detail", "flow": detail}
        else:
            result = {
                "mode": "candidates",
                "candidates": [
                    {
                        "flow_id": c["flow_id"],
                        "type": c["type"],
                        "name": c["name"],
                        "entry_fqn": c["entry_fqn"],
                    }
                    for c in candidates
                ],
            }

    if output_json:
        print(json_mod.dumps(result, indent=2))
    elif result["mode"] == "list":
        rows = result["flows"]
        if not rows:
            console.print("[yellow]No flows found.[/yellow]")
        else:
            table = Table(title=f"Flows ({len(rows)})")
            table.add_column("Type", style="cyan")
            table.add_column("Name", style="green")
            table.add_column("Entry FQN", style="yellow")
            table.add_column("Flow ID", style="dim")
            for f in rows:
                table.add_row(f["type"], f["name"], f["entry_fqn"], f["flow_id"])
            console.print(table)
    elif result["mode"] == "candidates":
        cands = result["candidates"]
        if not cands:
            console.print(f"[yellow]No flows match '{query}'.[/yellow]")
        else:
            console.print(f"[bold]Multiple matches for '{query}':[/bold]")
            table = Table()
            table.add_column("Type", style="cyan")
            table.add_column("Name", style="green")
            table.add_column("Flow ID", style="dim")
            for c in cands:
                table.add_row(c["type"], c["name"], c["flow_id"])
            console.print(table)
    else:
        flow = result["flow"]
        console.print(f"\n[bold]{flow['name']}[/bold] ([cyan]{flow['type']}[/cyan])")
        console.print(f"[dim]flow_id:[/dim] {flow['flow_id']}")
        entry = flow["entry"]
        loc = entry.get("file") or "<unknown>"
        if entry.get("start_line") and entry.get("end_line"):
            loc = f"{loc}:{entry['start_line']}-{entry['end_line']}"
        console.print(f"[dim]entry:[/dim]   {entry['fqn']}  [dim]{loc}[/dim]")
        if flow["type"] == "http":
            console.print(f"[dim]route:[/dim]   {flow.get('route','')} {' '.join(flow.get('http_methods',[]))}")
        elif flow["type"] == "message":
            console.print(f"[dim]message:[/dim] {flow.get('message_class','')}")
        elif flow["type"] == "event":
            console.print(f"[dim]event:[/dim]   {flow.get('event_name','')}")
        elif flow["type"] == "cli":
            console.print(f"[dim]command:[/dim] {flow.get('command_name','')}")
        if flow["triggers_out"]:
            console.print(f"\n[bold]Triggers out ({len(flow['triggers_out'])}):[/bold]")
            for t in flow["triggers_out"]:
                console.print(f"  → [cyan]{t['trigger_type']}[/cyan] via [yellow]{t['via']}[/yellow] → {t['target_name']} [dim]({t['target_flow_id']})[/dim]")
        if flow["triggers_in"]:
            console.print(f"\n[bold]Triggers in ({len(flow['triggers_in'])}):[/bold]")
            for t in flow["triggers_in"]:
                console.print(f"  ← [cyan]{t['trigger_type']}[/cyan] via [yellow]{t['via']}[/yellow] ← {t['source_name']} [dim]({t['source_flow_id']})[/dim]")
        if not flow["triggers_out"] and not flow["triggers_in"]:
            console.print("\n[dim](no triggers)[/dim]")

    conn.close()


@app.command()
def source(
    symbol: str = typer.Argument(..., help="Symbol to read source for"),
    project_root: str = typer.Option(None, "--project-root", "-r", help="Path to PHP project root"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show raw source code for a node using its file + line range."""
    import json as json_mod

    from .ai.config import AIConfig
    from .ai.source_reader import SourceReader
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.queries.resolve import resolve_symbol as do_resolve
    from .db.query_runner import QueryRunner

    neo4j_config = Neo4jConfig.from_env()
    ai_config = AIConfig.from_env()
    if project_root:
        ai_config.project_root = project_root
    if not ai_config.project_root:
        console.print("[red]project_root is required (use --project-root or KLOC_PROJECT_ROOT)[/red]")
        raise typer.Exit(1)

    conn = Neo4jConnection(neo4j_config)
    runner = QueryRunner(conn)

    candidates = do_resolve(runner, symbol)
    if not candidates:
        console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    node = candidates[0]
    reader = SourceReader(ai_config.project_root)
    src = reader.read_node_source(node)
    if not src:
        console.print(f"[red]Could not read source for {node.fqn} (file={node.file})[/red]")
        raise typer.Exit(1)

    start = node.enclosing_start_line if node.enclosing_start_line is not None else node.start_line
    end = node.enclosing_end_line if node.enclosing_end_line is not None else node.end_line
    start_1b = (start + 1) if start is not None else None
    end_1b = (end + 1) if end is not None else None

    if output_json:
        out = {
            "node_id": node.node_id,
            "kind": node.kind,
            "fqn": node.fqn,
            "file": node.file,
            "start_line": start_1b,
            "end_line": end_1b,
            "lines": (end - start + 1) if start is not None and end is not None else None,
            "chars": len(src),
            "tokens_estimate": SourceReader.estimate_tokens(src),
            "content": src,
        }
        print(json_mod.dumps(out, indent=2))
    else:
        loc = node.file or "<unknown>"
        if start_1b is not None and end_1b is not None:
            loc = f"{loc}:{start_1b}-{end_1b}"
        console.print(f"\n[bold]{node.fqn}[/bold] ({node.kind})")
        console.print(
            f"[dim]{loc}  ({len(src)} chars, ~{SourceReader.estimate_tokens(src)} tokens)[/dim]\n"
        )
        console.print(src)

    conn.close()


@app.command()
def chunks(
    symbol: str = typer.Argument(..., help="Symbol to chunk source for"),
    project_root: str = typer.Option(None, "--project-root", "-r", help="Path to PHP project root"),
    max_tokens: int = typer.Option(8000, "--max-tokens", help="Max tokens per chunk"),
    output_json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show chunked source code for a node (the same chunks used for embedding)."""
    import json as json_mod

    from .ai.chunker import CodeChunker
    from .ai.config import AIConfig
    from .ai.source_reader import SourceReader
    from .config import Neo4jConfig
    from .db.connection import Neo4jConnection
    from .db.queries.resolve import resolve_symbol as do_resolve
    from .db.query_runner import QueryRunner
    from .db.result_mapper import records_to_nodes

    neo4j_config = Neo4jConfig.from_env()
    ai_config = AIConfig.from_env()
    if project_root:
        ai_config.project_root = project_root
    if not ai_config.project_root:
        console.print("[red]project_root is required (use --project-root or KLOC_PROJECT_ROOT)[/red]")
        raise typer.Exit(1)

    conn = Neo4jConnection(neo4j_config)
    runner = QueryRunner(conn)

    candidates = do_resolve(runner, symbol)
    if not candidates:
        console.print(f"[red]Symbol not found: {symbol}[/red]")
        raise typer.Exit(1)

    node = candidates[0]
    reader = SourceReader(ai_config.project_root)
    src = reader.read_node_source(node)
    if not src:
        console.print(f"[red]Could not read source for {node.fqn} (file={node.file})[/red]")
        raise typer.Exit(1)

    method_sources = None
    if node.kind in ("Class", "Interface", "Trait", "Enum"):
        records = runner.execute(
            "MATCH (c:Node {node_id: $cid})-[:CONTAINS]->(m:Node) "
            "WHERE m.kind = 'Method' RETURN m ORDER BY m.start_line",
            cid=node.node_id,
        )
        method_sources = []
        for m in records_to_nodes(records, key="m"):
            ms = reader.read_node_source(m)
            if ms:
                method_sources.append((m, ms))

    chunker = CodeChunker(max_tokens=max_tokens)
    chunk_list = chunker.chunk_node(node, src, method_sources=method_sources)

    if output_json:
        out = {
            "node_id": node.node_id,
            "kind": node.kind,
            "fqn": node.fqn,
            "file": node.file,
            "max_tokens": max_tokens,
            "total_chunks": len(chunk_list),
            "chunks": [
                {
                    "index": c.chunk_index,
                    "total": c.total_chunks,
                    "tokens_estimate": c.token_estimate,
                    "chars": len(c.content),
                    "content": c.content,
                }
                for c in chunk_list
            ],
        }
        print(json_mod.dumps(out, indent=2))
    else:
        console.print(
            f"\n[bold]{node.fqn}[/bold] ({node.kind}) — {len(chunk_list)} chunk(s), "
            f"max {max_tokens} tokens each\n"
        )
        for c in chunk_list:
            console.print(
                f"[dim]── chunk {c.chunk_index + 1}/{c.total_chunks} "
                f"(~{c.token_estimate} tokens, {len(c.content)} chars) ──[/dim]"
            )
            console.print(c.content)
            console.print()

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
