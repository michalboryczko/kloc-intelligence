"""Pure logic helper functions for graph data.

Ported from kloc-cli's graph_utils.py. These are standalone functions
that take data parameters instead of SoTIndex, suitable for use with
pre-fetched data from Neo4j.
"""

from .reference_types import REF_TYPE_PRIORITY


def member_display_name(kind: str, name: str) -> str:
    """Format a short member display name: '$prop', 'method()', 'CONST'.

    Args:
        kind: Node kind (e.g., "Method", "Property").
        name: Node name (e.g., "save", "$repo").

    Returns:
        Formatted display name.
    """
    if kind in ("Method", "Function"):
        return f"{name}()"
    if kind == "Property":
        return name if name.startswith("$") else f"${name}"
    return name


def sort_entries_by_priority(entries: list[dict], ref_type_priority: dict | None = None) -> list[dict]:
    """Sort context entries by reference type priority, then by file/line.

    Args:
        entries: List of context entry dicts with "ref_type", "file", "line" keys.
        ref_type_priority: Priority mapping (lower = higher priority). Defaults
            to REF_TYPE_PRIORITY.

    Returns:
        Sorted copy of the entries list.
    """
    if ref_type_priority is None:
        ref_type_priority = REF_TYPE_PRIORITY

    def sort_key(e: dict) -> tuple:
        pri = ref_type_priority.get(e.get("ref_type", ""), 10)
        return (pri, e.get("file", "") or "", e.get("line", 0) or 0)

    return sorted(entries, key=sort_key)


def sort_entries_by_location(entries: list[dict]) -> list[dict]:
    """Sort context entries by file path and line number.

    Args:
        entries: List of context entry dicts with "file" and "line" keys.

    Returns:
        Sorted copy of the entries list.
    """
    return sorted(entries, key=lambda e: (e.get("file", "") or "", e.get("line", 0) or 0))


def format_method_fqn(fqn: str, kind: str) -> str:
    """Ensure Method FQNs end with () for display.

    Args:
        fqn: The fully qualified name.
        kind: The node kind.

    Returns:
        FQN with () appended if it's a Method and doesn't already end with ().
    """
    if kind == "Method" and not fqn.endswith("()"):
        return fqn + "()"
    return fqn


def is_internal_reference(source_ancestors: list[str], target_class_id: str) -> bool:
    """Check if a source node is internal to the target class.

    A reference is internal if the target class appears in the source's
    containment ancestry chain.

    Args:
        source_ancestors: List of ancestor node IDs from source up to File.
        target_class_id: The class being queried.

    Returns:
        True if the source is internal to the target class.
    """
    return target_class_id in source_ancestors
