"""sot.json parser and Neo4j batch importer for kloc-intelligence."""

import re
from pathlib import Path
from typing import Optional

import msgspec

from .connection import Neo4jConnection

# Regex for signature extraction (ported from kloc-cli/src/graph/loader.py)
_RE_VISIBILITY = re.compile(
    r'^(?:public\s+|protected\s+|private\s+|static\s+|final\s+|abstract\s+)*function\s+'
)
_RE_ATTRIBUTES = re.compile(r'#\[[^\]]*\]\s*')
_RE_WHITESPACE = re.compile(r'\s+')

BATCH_SIZE = 5000

KIND_TO_LABEL = {
    "Class": "Class",
    "Interface": "Interface",
    "Trait": "Trait",
    "Enum": "Enum",
    "Method": "Method",
    "Function": "Function",
    "Property": "Property",
    "Const": "Const",
    "EnumCase": "EnumCase",
    "Argument": "Argument",
    "Value": "Value",
    "Call": "Call",
    "File": "File",
}

EDGE_TYPE_TO_REL = {
    "contains": "CONTAINS",
    "uses": "USES",
    "extends": "EXTENDS",
    "implements": "IMPLEMENTS",
    "overrides": "OVERRIDES",
    "type_hint": "TYPE_HINT",
    "calls": "CALLS",
    "receiver": "RECEIVER",
    "argument": "ARGUMENT",
    "produces": "PRODUCES",
    "assigned_from": "ASSIGNED_FROM",
    "type_of": "TYPE_OF",
    "return_type": "RETURN_TYPE",
}


# --- msgspec structs for sot.json parsing ---


class RangeSpec(msgspec.Struct, omit_defaults=True):
    start_line: int
    start_col: int
    end_line: int
    end_col: int


class LocationSpec(msgspec.Struct, omit_defaults=True):
    file: str
    line: int
    col: Optional[int] = None


class NodeSpec(msgspec.Struct, omit_defaults=True):
    id: str
    kind: str
    name: str
    fqn: str
    symbol: str
    file: Optional[str] = None
    range: Optional[dict] = None
    documentation: list[str] = []
    value_kind: Optional[str] = None
    type_symbol: Optional[str] = None
    call_kind: Optional[str] = None
    enclosing_range: Optional[dict] = None


class EdgeSpec(msgspec.Struct, omit_defaults=True):
    type: str
    source: str
    target: str
    location: Optional[dict] = None
    position: Optional[int] = None
    expression: Optional[str] = None
    parameter: Optional[str] = None


class SoTSpec(msgspec.Struct, omit_defaults=True):
    version: str = "1.0"
    metadata: dict = {}
    nodes: list[NodeSpec] = []
    edges: list[EdgeSpec] = []


_decoder = msgspec.json.Decoder(SoTSpec)


def load_sot(path: str | Path) -> SoTSpec:
    """Load and decode a sot.json file."""
    with open(path, "rb") as f:
        return _decoder.decode(f.read())


# --- Signature extraction (ported from kloc-cli/src/graph/loader.py) ---


def extract_signature(node: NodeSpec) -> Optional[str]:
    """Extract method/function signature from documentation."""
    if not node.documentation or node.kind not in ("Method", "Function"):
        return None
    for doc in node.documentation:
        clean = doc.replace("```php", "").replace("```", "").strip()
        if "function " in clean:
            sig_lines = []
            capturing = False
            for line in clean.split("\n"):
                line = line.strip()
                if "function " in line:
                    capturing = True
                if capturing:
                    sig_lines.append(line)
                    if ")" in line:
                        break
            if not sig_lines:
                continue
            full_sig = " ".join(sig_lines)
            full_sig = _RE_VISIBILITY.sub('', full_sig)
            full_sig = _RE_ATTRIBUTES.sub('', full_sig)
            full_sig = _RE_WHITESPACE.sub(' ', full_sig).strip()
            if "(" in full_sig and ")" in full_sig:
                return full_sig
            if "(" in full_sig:
                method_name = full_sig.split("(")[0]
                return f"{method_name}(...)"
            return full_sig
    return None


# --- Property mapping ---


def node_to_props(node: NodeSpec) -> dict:
    """Convert a NodeSpec to a flat dict of Neo4j properties."""
    props = {
        "node_id": node.id,
        "kind": node.kind,
        "name": node.name,
        "fqn": node.fqn,
        "symbol": node.symbol,
    }
    if node.file:
        props["file"] = node.file
    if node.range:
        props["start_line"] = node.range.get("start_line")
        props["start_col"] = node.range.get("start_col")
        props["end_line"] = node.range.get("end_line")
        props["end_col"] = node.range.get("end_col")
    if node.enclosing_range:
        props["enclosing_start_line"] = node.enclosing_range.get("start_line")
        props["enclosing_start_col"] = node.enclosing_range.get("start_col")
        props["enclosing_end_line"] = node.enclosing_range.get("end_line")
        props["enclosing_end_col"] = node.enclosing_range.get("end_col")
    if node.documentation:
        props["documentation"] = node.documentation
    if node.value_kind:
        props["value_kind"] = node.value_kind
    if node.type_symbol:
        props["type_symbol"] = node.type_symbol
    if node.call_kind:
        props["call_kind"] = node.call_kind
    # Compute and store signature for Method/Function nodes
    sig = extract_signature(node)
    if sig:
        props["signature"] = sig
    return props


def edge_to_props(edge: EdgeSpec) -> dict:
    """Convert an EdgeSpec to a flat dict of Neo4j properties."""
    props = {
        "type": edge.type,
        "source_id": edge.source,
        "target_id": edge.target,
    }
    if edge.location:
        props["loc_file"] = edge.location.get("file")
        props["loc_line"] = edge.location.get("line")
    if edge.position is not None:
        props["position"] = edge.position
    if edge.expression:
        props["expression"] = edge.expression
    if edge.parameter:
        props["parameter"] = edge.parameter
    return props


def parse_sot(sot_path: str | Path) -> tuple[list[dict], list[dict]]:
    """Parse a sot.json file and return (node_props_list, edge_props_list).

    Adds an ``ordinal`` property to CONTAINS edges to preserve the sot.json
    insertion order (Neo4j does not guarantee relationship order).
    """
    data = load_sot(sot_path)
    nodes = [node_to_props(n) for n in data.nodes]

    # Track per-source ordinal for CONTAINS edges so definition queries
    # can reproduce the original sot.json child order.
    contains_ordinal: dict[str, int] = {}
    edges: list[dict] = []
    for e in data.edges:
        props = edge_to_props(e)
        if e.type == "contains":
            src = e.source
            ordinal = contains_ordinal.get(src, 0)
            props["ordinal"] = ordinal
            contains_ordinal[src] = ordinal + 1
        edges.append(props)

    return nodes, edges


# --- Batch import ---


def import_nodes(connection: Neo4jConnection, nodes: list[dict],
                 batch_size: int = BATCH_SIZE) -> int:
    """Import nodes into Neo4j with kind-specific labels, in batches."""
    by_kind: dict[str, list[dict]] = {}
    for node in nodes:
        by_kind.setdefault(node["kind"], []).append(node)
    total = 0
    for kind, kind_nodes in by_kind.items():
        label = KIND_TO_LABEL.get(kind, kind)
        query = f"UNWIND $batch AS props CREATE (n:Node:{label}) SET n = props"
        for i in range(0, len(kind_nodes), batch_size):
            batch = kind_nodes[i:i + batch_size]
            with connection.session() as session:
                session.run(query, batch=batch)
            total += len(batch)
    return total


def import_edges(connection: Neo4jConnection, edges: list[dict],
                 batch_size: int = BATCH_SIZE) -> int:
    """Import edges into Neo4j as typed relationships, in batches."""
    by_type: dict[str, list[dict]] = {}
    for edge in edges:
        by_type.setdefault(edge["type"], []).append(edge)
    total = 0
    for edge_type, type_edges in by_type.items():
        rel_type = EDGE_TYPE_TO_REL.get(edge_type, edge_type.upper())
        query = f"""
        UNWIND $batch AS props
        MATCH (source:Node {{node_id: props.source_id}})
        MATCH (target:Node {{node_id: props.target_id}})
        CREATE (source)-[r:{rel_type}]->(target)
        SET r.loc_file = props.loc_file,
            r.loc_line = props.loc_line,
            r.position = props.position,
            r.expression = props.expression,
            r.parameter = props.parameter,
            r.ordinal = props.ordinal
        """
        for i in range(0, len(type_edges), batch_size):
            batch = type_edges[i:i + batch_size]
            with connection.session() as session:
                session.run(query, batch=batch)
            total += len(batch)
    return total


# --- Validation ---


class ImportValidationError(Exception):
    pass


def validate_import(connection: Neo4jConnection, expected_nodes: int,
                    expected_edges: int) -> dict:
    """Validate that the import produced the expected counts."""
    with connection.session() as session:
        node_count = session.run(
            "MATCH (n:Node) RETURN count(n) AS cnt"
        ).single()["cnt"]
        kind_counts = {
            row["kind"]: row["cnt"]
            for row in session.run(
                "MATCH (n:Node) RETURN n.kind AS kind, count(n) AS cnt ORDER BY cnt DESC"
            )
        }
        edge_count = session.run(
            "MATCH ()-[r]->() RETURN count(r) AS cnt"
        ).single()["cnt"]
        type_counts = {
            row["type"]: row["cnt"]
            for row in session.run(
                "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS cnt ORDER BY cnt DESC"
            )
        }
    report = {
        "node_count": node_count,
        "expected_nodes": expected_nodes,
        "node_match": node_count == expected_nodes,
        "edge_count": edge_count,
        "expected_edges": expected_edges,
        "edge_match": edge_count == expected_edges,
        "kind_counts": kind_counts,
        "type_counts": type_counts,
        "valid": node_count == expected_nodes and edge_count == expected_edges,
    }
    if not report["valid"]:
        raise ImportValidationError(
            f"Import validation failed: "
            f"nodes {node_count}/{expected_nodes}, edges {edge_count}/{expected_edges}"
        )
    return report
