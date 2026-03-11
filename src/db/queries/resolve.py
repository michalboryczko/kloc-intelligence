"""Cypher queries for symbol resolution."""

from ..query_runner import QueryRunner
from ..result_mapper import records_to_nodes
from ...models.node import NodeData

# EXACT FQN: search ALL nodes (no kind filter!)
# Note: Cypher param named $search_term to avoid collision with neo4j driver's
# session.run(query=...) first positional parameter.
RESOLVE_EXACT_FQN = """
MATCH (n:Node {fqn: $search_term})
RETURN n
"""

# Case-insensitive: ALL nodes
RESOLVE_CASE_INSENSITIVE = """
MATCH (n:Node)
WHERE toLower(n.fqn) = toLower($search_term)
RETURN n
"""

# For fuzzy searches, limit to user-visible kinds
SEARCHABLE_KINDS = [
    "Class", "Interface", "Trait", "Enum",
    "Method", "Function",
    "Property", "Const", "EnumCase",
    "File",
]

RESOLVE_SUFFIX = """
MATCH (n:Node)
WHERE (n.fqn ENDS WITH $search_term OR n.fqn ENDS WITH ('::' + $search_term))
  AND n.kind IN $searchable_kinds
RETURN n
LIMIT 50
"""

RESOLVE_NAME = """
MATCH (n:Node {name: $search_term})
WHERE n.kind IN $searchable_kinds
RETURN n
LIMIT 50
"""

RESOLVE_CONTAINS = """
MATCH (n:Node)
WHERE n.fqn CONTAINS $search_term AND n.kind IN $searchable_kinds
RETURN n
LIMIT 50
"""


def resolve_symbol(runner: QueryRunner, query: str) -> list[NodeData]:
    """Resolve symbol, cascade matching kloc-cli exactly.

    Search order:
    1. Exact FQN (all kinds, dedup Value/Argument)
    2. Case-insensitive FQN (all kinds)
    3. Suffix match (searchable kinds only)
    4. Short name match (searchable kinds only)
    5. Name without parens
    6. Contains fallback
    """
    normalized = query.strip().lstrip("\\")

    # 1. Exact FQN (NO kind filter - must find Value nodes too)
    records = runner.execute(RESOLVE_EXACT_FQN, search_term=normalized)
    if records:
        records = _dedup_value_argument(records)
        return records_to_nodes(records)

    # 2. Case-insensitive
    records = runner.execute(RESOLVE_CASE_INSENSITIVE, search_term=normalized)
    if records:
        return records_to_nodes(records)

    # 3. Suffix match
    records = runner.execute(
        RESOLVE_SUFFIX, search_term=normalized, searchable_kinds=SEARCHABLE_KINDS
    )
    if records:
        return records_to_nodes(records)

    # 4. Short name
    short_name = normalized.split("::")[-1] if "::" in normalized else normalized
    records = runner.execute(
        RESOLVE_NAME, search_term=short_name, searchable_kinds=SEARCHABLE_KINDS
    )
    if records:
        return records_to_nodes(records)

    # 5. Name without parens
    short_no_parens = short_name.rstrip("()")
    if short_no_parens != short_name:
        records = runner.execute(
            RESOLVE_NAME, search_term=short_no_parens, searchable_kinds=SEARCHABLE_KINDS
        )
        if records:
            return records_to_nodes(records)

    # 6. Contains fallback
    records = runner.execute(
        RESOLVE_CONTAINS, search_term=normalized, searchable_kinds=SEARCHABLE_KINDS
    )
    return records_to_nodes(records)


def _dedup_value_argument(records):
    """When both Value and Argument nodes exist for same FQN, keep only Value."""
    if len(records) <= 1:
        return records
    has_value = any(r["n"]["kind"] == "Value" for r in records)
    if has_value:
        return [r for r in records if r["n"]["kind"] != "Argument"]
    return records
