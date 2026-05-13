"""Rich console renderers for :Flow / :Message / :Event / :HttpClient output.

Kept separate from cli.py so the CLI module stays a thin orchestrator (parse
args → query → call print_json or one of these printers). The MCP server
returns the same dicts the CLI emits with ``--json``; this module handles only
the Rich/console side.
"""

from rich.console import Console
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# :Flow rendering
# ---------------------------------------------------------------------------


def print_flows_list(rows: list[dict]) -> None:
    """Render a list of :Flow rows as a Rich table."""
    if not rows:
        console.print("[yellow]No flows found.[/yellow]")
        return
    table = Table(title=f"Flows ({len(rows)})")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Entry FQN", style="yellow")
    table.add_column("Flow ID", style="dim")
    for f in rows:
        table.add_row(f["type"], f["name"], f["entry_fqn"], f["flow_id"])
    console.print(table)


def print_flow_candidates(query: str, candidates: list[dict]) -> None:
    """Render a candidate list returned when a query partially matches."""
    if not candidates:
        console.print(f"[yellow]No flows match '{query}'.[/yellow]")
        return
    console.print(f"[bold]Multiple matches for '{query}':[/bold]")
    table = Table()
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Flow ID", style="dim")
    for c in candidates:
        table.add_row(c["type"], c["name"], c["flow_id"])
    console.print(table)


def print_flow_detail(flow: dict) -> None:
    """Render full detail for a single :Flow (v3 dispatches_in/_out shape)."""
    console.print(f"\n[bold]{flow['name']}[/bold] ([cyan]{flow['type']}[/cyan])")
    console.print(f"[dim]flow_id:[/dim] {flow['flow_id']}")
    entry = flow["entry"]
    loc = entry.get("file") or "<unknown>"
    if entry.get("start_line") and entry.get("end_line"):
        loc = f"{loc}:{entry['start_line']}-{entry['end_line']}"
    console.print(f"[dim]entry:[/dim]   {entry['fqn']}  [dim]{loc}[/dim]")
    if flow["type"] == "http":
        console.print(
            f"[dim]route:[/dim]   {flow.get('route', '')} {' '.join(flow.get('http_methods', []))}"
        )
    elif flow["type"] == "message":
        console.print(f"[dim]message:[/dim] {flow.get('message_class', '')}")
    elif flow["type"] == "event":
        console.print(f"[dim]event:[/dim]   {flow.get('event_name', '')}")
    elif flow["type"] == "cli":
        console.print(f"[dim]command:[/dim] {flow.get('command_name', '')}")
    if flow.get("explanation"):
        console.print(f"\n[bold]Summary:[/bold] {flow['explanation']}")
        model = flow.get("explain_model", "")
        if model:
            console.print(f"[dim]({model})[/dim]")

    out = flow.get("dispatches_out") or {}
    in_ = flow.get("dispatches_in") or {}
    out_msgs = out.get("messages") or []
    out_evts = out.get("events") or []
    out_https = out.get("http_clients") or []
    in_msgs = in_.get("messages") or []
    in_evts = in_.get("events") or []
    has_out = bool(out_msgs or out_evts or out_https)
    has_in = bool(in_msgs or in_evts)

    if has_out:
        console.print("\n[bold]Dispatches out:[/bold]")
        for m in out_msgs:
            transports = ", ".join(m.get("transports") or []) or "-"
            target_count = len(m.get("target_flow_ids") or [])
            console.print(
                f"  → [magenta]message[/magenta] {m['fqn']} "
                f"[dim](transports={transports}, handlers={target_count}, "
                f"from {m.get('caller_method_fqn') or '?'})[/dim]"
            )
        for e in out_evts:
            target_count = len(e.get("targets") or [])
            console.print(
                f"  → [blue]event[/blue] {e['fqn']} "
                f"[dim](subscribers={target_count}, "
                f"from {e.get('caller_method_fqn') or '?'})[/dim]"
            )
        for h in out_https:
            console.print(
                f"  → [yellow]http[/yellow] {h.get('service_id') or h['id']} "
                f"→ {h.get('base_uri') or '?'} "
                f"[dim](class={h.get('class_fqn') or '?'}, "
                f"from {h.get('caller_method_fqn') or '?'})[/dim]"
            )

    if has_in:
        console.print("\n[bold]Dispatches in:[/bold]")
        for m in in_msgs:
            sources_count = len(m.get("source_flow_ids") or [])
            console.print(
                f"  ← [magenta]message[/magenta] {m['fqn']} "
                f"[dim](dispatched by {sources_count} flow(s))[/dim]"
            )
        for e in in_evts:
            sources_count = len(e.get("source_flow_ids") or [])
            console.print(
                f"  ← [blue]event[/blue] {e['fqn']} "
                f"[dim](priority={e.get('priority', 0)}, "
                f"dispatched by {sources_count} flow(s))[/dim]"
            )

    if not has_out and not has_in:
        console.print("\n[dim](no dispatches)[/dim]")


# ---------------------------------------------------------------------------
# :Message rendering
# ---------------------------------------------------------------------------


def print_messages_list(rows: list[dict]) -> None:
    """Render the :Message list as a Rich table."""
    if not rows:
        console.print("[yellow]No messages found.[/yellow]")
        return
    table = Table(title=f"Messages ({len(rows)})")
    table.add_column("ID", style="dim")
    table.add_column("FQN", style="magenta")
    table.add_column("Transports", style="cyan")
    table.add_column("Sources", style="green", justify="right")
    table.add_column("Targets", style="yellow", justify="right")
    for r in rows:
        table.add_row(
            r["id"],
            r["fqn"],
            ", ".join(r.get("transports") or []) or "-",
            str(r["sources_count"]),
            str(r["targets_count"]),
        )
    console.print(table)


def print_message_candidates(query: str, candidates: list[dict]) -> None:
    if not candidates:
        console.print(f"[yellow]No messages match '{query}'.[/yellow]")
        return
    console.print(f"[bold]Multiple matches for '{query}':[/bold]")
    table = Table()
    table.add_column("ID", style="dim")
    table.add_column("FQN", style="magenta")
    for c in candidates:
        table.add_row(c["id"], c["fqn"])
    console.print(table)


def print_message_detail(detail: dict) -> None:
    console.print(f"\n[bold magenta]{detail['fqn']}[/bold magenta]")
    console.print(f"[dim]id:[/dim] {detail['id']}")
    transports = ", ".join(detail.get("transports") or []) or "-"
    console.print(f"[dim]transports:[/dim] {transports}")
    of_type = detail.get("of_type_class_fqn")
    if of_type:
        console.print(f"[dim]of_type:[/dim] {of_type}")

    sources = detail.get("sources") or []
    targets = detail.get("targets") or []

    if sources:
        console.print(f"\n[bold]Sources ({len(sources)}):[/bold]")
        for s in sources:
            console.print(
                f"  ← {s.get('caller_method_fqn') or '?'} "
                f"[dim]({s['flow_id']}, call_node_id={s.get('call_node_id') or '?'})[/dim]"
            )
    else:
        console.print("\n[dim](no sources)[/dim]")

    if targets:
        console.print(f"\n[bold]Targets ({len(targets)}):[/bold]")
        for t in targets:
            console.print(f"  → [cyan]{t['flow_id']}[/cyan]")
    else:
        console.print("\n[dim](no handlers — message has empty targets[])[/dim]")


# ---------------------------------------------------------------------------
# :Event rendering
# ---------------------------------------------------------------------------


def print_events_list(rows: list[dict]) -> None:
    if not rows:
        console.print("[yellow]No events found.[/yellow]")
        return
    table = Table(title=f"Events ({len(rows)})")
    table.add_column("ID", style="dim")
    table.add_column("FQN", style="blue")
    table.add_column("Sources", style="green", justify="right")
    table.add_column("Targets", style="yellow", justify="right")
    for r in rows:
        table.add_row(r["id"], r["fqn"], str(r["sources_count"]), str(r["targets_count"]))
    console.print(table)


def print_event_candidates(query: str, candidates: list[dict]) -> None:
    if not candidates:
        console.print(f"[yellow]No events match '{query}'.[/yellow]")
        return
    console.print(f"[bold]Multiple matches for '{query}':[/bold]")
    table = Table()
    table.add_column("ID", style="dim")
    table.add_column("FQN", style="blue")
    for c in candidates:
        table.add_row(c["id"], c["fqn"])
    console.print(table)


def print_event_detail(detail: dict) -> None:
    console.print(f"\n[bold blue]{detail['fqn']}[/bold blue]")
    console.print(f"[dim]id:[/dim] {detail['id']}")
    of_type = detail.get("of_type_class_fqn")
    if of_type:
        console.print(f"[dim]of_type:[/dim] {of_type}")

    sources = detail.get("sources") or []
    targets = detail.get("targets") or []

    if sources:
        console.print(f"\n[bold]Sources ({len(sources)}):[/bold]")
        for s in sources:
            console.print(
                f"  ← {s.get('caller_method_fqn') or '?'} "
                f"[dim]({s['flow_id']}, call_node_id={s.get('call_node_id') or '?'})[/dim]"
            )
    else:
        console.print("\n[dim](no sources)[/dim]")

    if targets:
        console.print(f"\n[bold]Subscribers ({len(targets)}):[/bold]")
        for t in targets:
            console.print(
                f"  → [cyan]{t['flow_id']}[/cyan] [dim](priority={t.get('priority', 0)})[/dim]"
            )
    else:
        console.print("\n[dim](no subscribers)[/dim]")


# ---------------------------------------------------------------------------
# :HttpClient rendering
# ---------------------------------------------------------------------------


def print_http_clients_list(rows: list[dict]) -> None:
    if not rows:
        console.print("[yellow]No HTTP clients found.[/yellow]")
        return
    table = Table(title=f"HTTP Clients ({len(rows)})")
    table.add_column("ID", style="dim")
    table.add_column("Service ID", style="yellow")
    table.add_column("Base URI", style="cyan")
    table.add_column("Class FQN", style="magenta")
    table.add_column("Sources", style="green", justify="right")
    for r in rows:
        table.add_row(
            r["id"],
            r["service_id"],
            r["base_uri"],
            r["class_fqn"],
            str(r["sources_count"]),
        )
    console.print(table)


def print_http_client_candidates(query: str, candidates: list[dict]) -> None:
    if not candidates:
        console.print(f"[yellow]No HTTP clients match '{query}'.[/yellow]")
        return
    console.print(f"[bold]Multiple matches for '{query}':[/bold]")
    table = Table()
    table.add_column("ID", style="dim")
    table.add_column("Service ID", style="yellow")
    table.add_column("Base URI", style="cyan")
    for c in candidates:
        table.add_row(c["id"], c["service_id"], c["base_uri"])
    console.print(table)


def print_http_client_detail(detail: dict) -> None:
    console.print(f"\n[bold yellow]{detail['service_id'] or detail['id']}[/bold yellow]")
    console.print(f"[dim]id:[/dim] {detail['id']}")
    console.print(f"[dim]base_uri:[/dim] {detail.get('base_uri') or '?'}")
    console.print(f"[dim]class_fqn:[/dim] {detail.get('class_fqn') or '?'}")
    of_type = detail.get("of_type_class_fqn")
    if of_type:
        console.print(f"[dim]of_type:[/dim] {of_type}")
    else:
        console.print("[dim]of_type:[/dim] (none — vendor class)")

    sources = detail.get("sources") or []
    if sources:
        console.print(f"\n[bold]Sources ({len(sources)}):[/bold]")
        for s in sources:
            console.print(
                f"  ← {s.get('caller_method_fqn') or '?'} "
                f"[dim]({s['flow_id']}, call_node_id={s.get('call_node_id') or '?'})[/dim]"
            )
    else:
        console.print("\n[dim](no sources)[/dim]")
