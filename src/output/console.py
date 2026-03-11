"""Rich console formatters for usages, deps, owners, inherit, overrides, and context output."""

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from ..models.results import (
    UsagesTreeResult,
    DepsTreeResult,
    UsageEntry,
    DepsEntry,
    OwnersResult,
    InheritTreeResult,
    InheritEntry,
    OverridesTreeResult,
    OverrideEntry,
    ContextResult,
    ContextEntry,
)

console = Console()


def _format_location(file: str | None, line: int | None) -> str:
    """Format file:line location string (1-based line numbers)."""
    if file and line is not None:
        return f"{file}:{line + 1}"
    elif file:
        return file
    return "<unknown>"


def print_usages_result(result: UsagesTreeResult) -> None:
    """Print usages result as a Rich table (flat) or tree (depth > 1)."""
    console.print(f"[bold]Usages of[/bold] {result.target.fqn}")
    console.print(f"  defined at: {result.target.location_str}")
    console.print()

    if not result.tree:
        console.print("[dim]No usages found[/dim]")
        return

    if result.max_depth == 1:
        _print_usages_flat(result)
    else:
        _print_usages_tree(result)


def _print_usages_flat(result: UsagesTreeResult) -> None:
    """Print flat usages as a table."""
    table = Table(title=f"Usages ({len(result.tree)} results)")
    table.add_column("File", style="yellow")
    table.add_column("Line")
    table.add_column("Referrer", style="green")

    for entry in result.tree:
        table.add_row(
            entry.file or "<unknown>",
            str(entry.line + 1) if entry.line is not None else "",
            entry.fqn,
        )

    console.print(table)


def _print_usages_tree(result: UsagesTreeResult) -> None:
    """Print usages as a Rich tree for depth > 1."""
    root = Tree(f"[bold]{result.target.fqn}[/bold]")
    _add_usage_children(root, result.tree)
    console.print(root)


def _add_usage_children(parent: Tree, entries: list[UsageEntry]) -> None:
    """Recursively add usage entries to a Rich tree."""
    for entry in entries:
        loc = _format_location(entry.file, entry.line)
        label = f"[{entry.depth}] {entry.fqn} ({loc})"
        node = parent.add(label)
        if entry.children:
            _add_usage_children(node, entry.children)


def print_deps_result(result: DepsTreeResult) -> None:
    """Print deps result as a Rich table (flat) or tree (depth > 1)."""
    console.print(f"[bold]Dependencies of[/bold] {result.target.fqn}")
    console.print(f"  defined at: {result.target.location_str}")
    console.print()

    if not result.tree:
        console.print("[dim]No dependencies found[/dim]")
        return

    if result.max_depth == 1:
        _print_deps_flat(result)
    else:
        _print_deps_tree(result)


def _print_deps_flat(result: DepsTreeResult) -> None:
    """Print flat deps as a table."""
    table = Table(title=f"Dependencies ({len(result.tree)} results)")
    table.add_column("File", style="yellow")
    table.add_column("Line")
    table.add_column("Dependency", style="green")

    for entry in result.tree:
        table.add_row(
            entry.file or "<unknown>",
            str(entry.line + 1) if entry.line is not None else "",
            entry.fqn,
        )

    console.print(table)


def _print_deps_tree(result: DepsTreeResult) -> None:
    """Print deps as a Rich tree for depth > 1."""
    root = Tree(f"[bold]{result.target.fqn}[/bold]")
    _add_deps_children(root, result.tree)
    console.print(root)


def _add_deps_children(parent: Tree, entries: list[DepsEntry]) -> None:
    """Recursively add deps entries to a Rich tree."""
    for entry in entries:
        loc = _format_location(entry.file, entry.line)
        label = f"[{entry.depth}] {entry.fqn} ({loc})"
        node = parent.add(label)
        if entry.children:
            _add_deps_children(node, entry.children)


# --- Owners ---


def print_owners_result(result: OwnersResult) -> None:
    """Print owners result as a flat chain display."""
    if not result.chain:
        console.print("[dim]No containment chain found[/dim]")
        return

    target = result.chain[0]
    console.print(f"[bold]Owners of[/bold] {target.fqn}")
    console.print()

    table = Table(title=f"Containment chain ({len(result.chain)} levels)")
    table.add_column("Level", style="dim")
    table.add_column("Kind", style="cyan")
    table.add_column("FQN", style="green")
    table.add_column("Location", style="yellow")

    for i, node in enumerate(result.chain):
        loc = _format_location(node.file, node.start_line)
        table.add_row(str(i), node.kind, node.fqn, loc)

    console.print(table)


# --- Inherit ---


def print_inherit_result(result: InheritTreeResult) -> None:
    """Print inherit result as a Rich tree."""
    direction_label = "ancestors" if result.direction == "up" else "descendants"
    console.print(
        f"[bold]Inheritance {direction_label} of[/bold] {result.root.fqn}"
    )
    console.print(f"  defined at: {result.root.location_str}")
    console.print()

    if not result.tree:
        console.print(f"[dim]No {direction_label} found[/dim]")
        return

    root = Tree(f"[bold]{result.root.fqn}[/bold] ({result.root.kind})")
    _add_inherit_children(root, result.tree)
    console.print(root)


def _add_inherit_children(
    parent: Tree, entries: list[InheritEntry]
) -> None:
    """Recursively add inherit entries to a Rich tree."""
    for entry in entries:
        loc = _format_location(entry.file, entry.line)
        label = f"[{entry.depth}] {entry.fqn} ({entry.kind}) ({loc})"
        node = parent.add(label)
        if entry.children:
            _add_inherit_children(node, entry.children)


# --- Overrides ---


def print_overrides_result(result: OverridesTreeResult) -> None:
    """Print overrides result as a Rich tree."""
    direction_label = (
        "overridden by" if result.direction == "down" else "overrides"
    )
    console.print(
        f"[bold]Method {direction_label}[/bold] {result.root.fqn}"
    )
    console.print(f"  defined at: {result.root.location_str}")
    console.print()

    if not result.tree:
        console.print("[dim]No overrides found[/dim]")
        return

    root = Tree(f"[bold]{result.root.fqn}[/bold]")
    _add_override_children(root, result.tree)
    console.print(root)


def _add_override_children(
    parent: Tree, entries: list[OverrideEntry]
) -> None:
    """Recursively add override entries to a Rich tree."""
    for entry in entries:
        loc = _format_location(entry.file, entry.line)
        label = f"[{entry.depth}] {entry.fqn} ({loc})"
        node = parent.add(label)
        if entry.children:
            _add_override_children(node, entry.children)


# --- Context ---


def _format_entry_name(entry: ContextEntry) -> str:
    """Format entry name, using signature for methods if available."""
    if entry.signature and entry.kind in ("Method", "Function"):
        if "::" in entry.fqn:
            class_part = entry.fqn.rsplit("::", 1)[0]
            return f"{class_part}::{entry.signature}"
        return entry.signature
    return entry.fqn


def _format_argument_lines(arg, indent: str = "          ") -> str:
    """Format a single argument with rich display.

    Format: param_fqn (type): `expression` ref_symbol
    With optional source_chain expansion below.
    """
    param = arg.param_fqn or arg.param_name or f"arg[{arg.position}]"
    type_suffix = f" ({arg.value_type})" if arg.value_type else ""
    value = arg.value_expr or "?"

    ref_suffix = ""
    if arg.value_ref_symbol:
        ref_suffix = f" {arg.value_ref_symbol}"
    elif arg.value_source == "literal":
        ref_suffix = " literal"

    line = f"\n{indent}{param}{type_suffix}: `{value}`{ref_suffix}"

    if arg.source_chain:
        for step in arg.source_chain:
            step_fqn = step.get("fqn", "?")
            step_ref = (
                f" [cyan]\\[{step['reference_type']}][/cyan]"
                if step.get("reference_type") else ""
            )
            line += f"\n{indent}    [dim]source:[/dim] {step_fqn}{step_ref}"
            if step.get("on"):
                on_text = step["on"]
                if step.get("on_kind"):
                    on_text += f" [cyan]\\[{step['on_kind']}][/cyan]"
                if step.get("on_file") and step.get("on_line") is not None:
                    on_text += (
                        f" [dim]({step['on_file']}:{step['on_line'] + 1})[/dim]"
                    )
                line += f"\n{indent}        [dim]on:[/dim] [green]{on_text}[/green]"

    return line


def print_definition_section(result: ContextResult) -> None:
    """Print the DEFINITION section for a context query result."""
    defn = result.definition
    if not defn:
        return

    console.print("[bold cyan]== DEFINITION ==[/bold cyan]")

    if defn.signature:
        console.print(f"[bold]{defn.signature}[/bold]")
    else:
        kind_display = defn.kind
        if defn.kind == "Value" and defn.value_kind:
            kind_display = f"{defn.kind} ({defn.value_kind})"
        console.print(f"[bold]{kind_display}[/bold]: {defn.fqn}")

    if defn.type_info:
        type_display = defn.type_info.get("name", defn.type_info.get("fqn", "?"))
        console.print(f"  [dim]Type:[/dim] {type_display}")

    if defn.source:
        source_name = defn.source.get("method_name", "unknown")
        source_line = defn.source.get("line")
        if source_line is not None:
            console.print(
                f"  [dim]Source:[/dim] {source_name} result (line {source_line + 1})"
            )
        else:
            console.print(f"  [dim]Source:[/dim] {source_name}")

    if defn.kind == "Value" and defn.declared_in:
        scope_fqn = defn.declared_in.get("fqn", "?")
        console.print(f"  [dim]Scope:[/dim] {scope_fqn}")

    if defn.arguments:
        console.print("  [dim]Arguments:[/dim]")
        for arg in defn.arguments:
            arg_name = arg.get("name", "?")
            arg_type = arg.get("type")
            if arg_type:
                console.print(f"    {arg_name}: {arg_type}")
            else:
                console.print(f"    {arg_name}")

    if defn.return_type and defn.kind not in ("Property",):
        type_name = defn.return_type.get(
            "name", defn.return_type.get("fqn", "?")
        )
        console.print(f"  [dim]Return type:[/dim] {type_name}")

    if defn.kind == "Property" and defn.return_type:
        rt = defn.return_type
        type_name = rt.get("name", rt.get("fqn", "?"))
        console.print(f"  [dim]Type:[/dim] {type_name}")
        type_fqn = rt.get("fqn")
        if type_fqn and type_fqn != type_name:
            console.print(f"  [dim]Type FQN:[/dim] {type_fqn}")
        vis = rt.get("visibility")
        if vis:
            console.print(f"  [dim]Visibility:[/dim] {vis}")
        console.print(
            f"  [dim]Promoted:[/dim] {'yes' if rt.get('promoted') else 'no'}"
        )
        console.print(
            f"  [dim]Readonly:[/dim] {'yes' if rt.get('readonly') else 'no'}"
        )
        if rt.get("static"):
            console.print("  [dim]Static:[/dim] yes")

    if defn.constructor_deps:
        console.print("  [dim]Constructor deps:[/dim]")
        for dep in defn.constructor_deps:
            dep_name = dep.get("name", "?")
            dep_type = dep.get("type")
            if dep_type:
                console.print(f"    {dep_name}: {dep_type}")
            else:
                console.print(f"    {dep_name}")

    if defn.properties:
        console.print("  [dim]Properties:[/dim]")
        for prop in defn.properties:
            prop_name = prop.get("name", "?")
            prop_type = prop.get("type")
            parts = []
            visibility = prop.get("visibility")
            if visibility:
                parts.append(visibility)
            if prop.get("readonly"):
                parts.append("readonly")
            if prop.get("static"):
                parts.append("static")
            if prop.get("promoted"):
                parts.append("promoted")
            prefix = f"[dim]{' '.join(parts)}[/dim] " if parts else ""
            if prop_type:
                console.print(f"    {prefix}{prop_name}: {prop_type}")
            else:
                console.print(f"    {prefix}{prop_name}")

    if defn.methods:
        console.print("  [dim]Methods:[/dim]")
        for method in defn.methods:
            sig = method.get("signature")
            tags = method.get("tags", [])
            tag_str = (
                f" [cyan]{''.join(f'[{t}]' for t in tags)}[/cyan]"
                if tags else ""
            )
            if sig:
                console.print(f"    {sig}{tag_str}")
            else:
                console.print(f"    {method.get('name', '?')}(){tag_str}")

    if defn.extends:
        console.print(f"  [dim]Extends:[/dim] {defn.extends}")
    if defn.implements:
        console.print(
            f"  [dim]Implements:[/dim] {', '.join(defn.implements)}"
        )
    if defn.uses_traits:
        console.print(
            f"  [dim]Uses traits:[/dim] {', '.join(defn.uses_traits)}"
        )

    if defn.declared_in and defn.kind != "Value":
        declared_fqn = defn.declared_in.get("fqn", "?")
        declared_file = defn.declared_in.get("file")
        declared_line = defn.declared_in.get("line")
        location = ""
        if declared_file:
            location = f" ({declared_file}"
            if declared_line is not None:
                location += f":{declared_line + 1}"
            location += ")"
        console.print(
            f"  [dim]Defined in:[/dim] {declared_fqn}{location}"
        )
    elif defn.file:
        location = defn.file
        if defn.line is not None:
            location += f":{defn.line + 1}"
        label = "File" if defn.kind == "Value" else "Defined at"
        console.print(f"  [dim]{label}:[/dim] {location}")

    console.print()


def _add_context_children(
    parent: Tree,
    entries: list[ContextEntry],
    show_impl: bool = False,
) -> None:
    """Recursively add context entries to a Rich tree."""
    for entry in entries:
        # Handle via_interface entries
        if entry.via_interface:
            display_name = _format_entry_name(entry)
            label = (
                f"[bold magenta]<- via interface:[/bold magenta] "
                f"{display_name}"
            )
            if entry.file and entry.line is not None:
                label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
            elif entry.file:
                label += f" [dim]({entry.file})[/dim]"
            branch = parent.add(label)
            if entry.children:
                _add_context_children(branch, entry.children, show_impl)
            continue

        # Variable entry
        if entry.entry_type == "local_variable":
            var_type_str = (
                f" ({entry.variable_type})" if entry.variable_type else ""
            )
            label = (
                f"[dim]\\[{entry.depth}][/dim] "
                f"[bold green]{entry.variable_name}[/bold green]"
                f"{var_type_str} [cyan]\\[variable][/cyan]"
            )
            if entry.file and entry.line is not None:
                label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
            elif entry.file:
                label += f" [dim]({entry.file})[/dim]"
            if entry.source_call:
                sc = entry.source_call
                sc_ref_type = (
                    f" [cyan]\\[{sc.member_ref.reference_type}][/cyan]"
                    if sc.member_ref and sc.member_ref.reference_type
                    else ""
                )
                label += f"\n        [dim]source:[/dim] {sc.fqn}{sc_ref_type}"
                if sc.member_ref and sc.member_ref.access_chain:
                    chain_text = sc.member_ref.access_chain
                    if sc.member_ref.access_chain_symbol:
                        chain_text += (
                            f" ({sc.member_ref.access_chain_symbol})"
                        )
                    if sc.member_ref.on_kind:
                        chain_text += (
                            f" [cyan]\\[{sc.member_ref.on_kind}][/cyan]"
                        )
                    if (
                        sc.member_ref.on_file
                        and sc.member_ref.on_line is not None
                    ):
                        chain_text += (
                            f" [dim]({sc.member_ref.on_file}"
                            f":{sc.member_ref.on_line + 1})[/dim]"
                        )
                    label += (
                        f"\n          [dim]on:[/dim] "
                        f"[green]{chain_text}[/green]"
                    )
                if sc.arguments:
                    label += "\n          [dim]args:[/dim]"
                    for arg in sc.arguments:
                        label += _format_argument_lines(
                            arg, indent="            "
                        )
        else:
            # Call entry or type reference
            display_name = _format_entry_name(entry)
            label = f"[dim]\\[{entry.depth}][/dim] {display_name}"
            if entry.member_ref:
                if entry.member_ref.target_name:
                    label += (
                        f" [bold yellow]->[/bold yellow] "
                        f"[yellow]{entry.member_ref.target_name}[/yellow]"
                    )
                if entry.member_ref.reference_type:
                    label += (
                        f" [cyan]\\[{entry.member_ref.reference_type}]"
                        f"[/cyan]"
                    )
            if entry.property_name and not (
                entry.member_ref and entry.member_ref.target_name
            ):
                label += (
                    f" [bold yellow]->[/bold yellow] "
                    f"[yellow]{entry.property_name}[/yellow]"
                )
            if entry.ref_type and not (
                entry.member_ref and entry.member_ref.reference_type
            ):
                label += f" [cyan]\\[{entry.ref_type}][/cyan]"
            if entry.callee and entry.ref_type == "method_call" and not (
                entry.member_ref and entry.member_ref.target_name
            ):
                label += (
                    f" [bold yellow]->[/bold yellow] "
                    f"[yellow]{entry.callee}[/yellow]"
                )
            if entry.via:
                via_short = (
                    entry.via.rsplit("\\", 1)[-1]
                    if "\\" in entry.via
                    else entry.via
                )
                label += f" [magenta]<- via {via_short}[/magenta]"
            if entry.sites:
                count = len(entry.sites)
                label += f" [dim](x{count})[/dim]"
                if entry.file:
                    label += f" [dim]({entry.file})[/dim]"
            elif entry.file and entry.line is not None:
                label += f" [dim]({entry.file}:{entry.line + 1})[/dim]"
            elif entry.file:
                label += f" [dim]({entry.file})[/dim]"
            if entry.on and not (
                entry.member_ref and entry.member_ref.access_chain
            ):
                on_text = entry.on
                if entry.on_kind:
                    on_text += f" [cyan]\\[{entry.on_kind}][/cyan]"
                label += (
                    f"\n        [dim]on:[/dim] [green]{on_text}[/green]"
                )
            elif entry.member_ref and entry.member_ref.access_chain:
                chain_text = entry.member_ref.access_chain
                if entry.member_ref.access_chain_symbol:
                    chain_text += (
                        f" ({entry.member_ref.access_chain_symbol})"
                    )
                if entry.member_ref.on_kind:
                    chain_text += (
                        f" [cyan]\\[{entry.member_ref.on_kind}][/cyan]"
                    )
                if (
                    entry.member_ref.on_file
                    and entry.member_ref.on_line is not None
                ):
                    chain_text += (
                        f" [dim]({entry.member_ref.on_file}"
                        f":{entry.member_ref.on_line + 1})[/dim]"
                    )
                label += (
                    f"\n        [dim]on:[/dim] [green]{chain_text}[/green]"
                )
            if entry.arguments:
                label += "\n        [dim]args:[/dim]"
                for arg in entry.arguments:
                    label += _format_argument_lines(arg, indent="          ")
            if entry.result_var:
                label += (
                    f"\n        [dim]result ->[/dim] "
                    f"[green]{entry.result_var}[/green]"
                )

        branch = parent.add(label)

        if entry.crossed_from:
            branch.add(
                f"[dim italic]crosses into {entry.crossed_from}[/dim italic]"
            )

        if show_impl and entry.implementations:
            for impl in entry.implementations:
                impl_display = _format_entry_name(impl)
                impl_label = (
                    f"[bold magenta]-> impl:[/bold magenta] {impl_display}"
                )
                if impl.file and impl.line is not None:
                    impl_label += (
                        f" [dim]({impl.file}:{impl.line + 1})[/dim]"
                    )
                elif impl.file:
                    impl_label += f" [dim]({impl.file})[/dim]"
                impl_branch = branch.add(impl_label)
                if impl.children:
                    _add_context_children(
                        impl_branch, impl.children, show_impl
                    )

        if entry.children:
            _add_context_children(branch, entry.children, show_impl)


def print_context_tree(result: ContextResult) -> None:
    """Print context (used_by and uses) as nested trees.

    Shows target info, definition section, USED BY tree, and USES tree.
    """
    target_display = result.target.display_name

    console.print(f"[bold]Context for {target_display}[/bold]")
    console.print(
        f"[dim]defined at: {result.target.location_str}[/dim]"
    )
    console.print()

    if result.definition:
        print_definition_section(result)

    # USED BY tree
    console.print("[bold cyan]== USED BY ==[/bold cyan]")
    if not result.used_by:
        console.print("[dim]None[/dim]")
    else:
        used_by_root = Tree(f"[bold]{target_display}[/bold]")
        _add_context_children(
            used_by_root, result.used_by, show_impl=False
        )
        console.print(used_by_root)

    console.print()

    # USES tree
    console.print("[bold cyan]== USES ==[/bold cyan]")
    if not result.uses:
        console.print("[dim]None[/dim]")
    else:
        uses_root = Tree(f"[bold]{target_display}[/bold]")
        _add_context_children(
            uses_root, result.uses, show_impl=True
        )
        console.print(uses_root)


def context_tree_to_dict(result: ContextResult) -> dict:
    """Convert context result to JSON-serializable dict.

    Delegates to ContextOutput model for contract-compliant serialization.
    """
    from ..models.output import ContextOutput

    return ContextOutput.from_result(result).to_dict()
