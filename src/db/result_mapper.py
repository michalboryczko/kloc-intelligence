"""Map Neo4j records to domain model objects."""

from neo4j import Record

from ..models.node import NodeData


def record_to_node(record: Record, key: str = "n") -> NodeData:
    """Convert a Neo4j Record containing a node to a NodeData instance."""
    node = record[key]
    return NodeData(
        node_id=node["node_id"],
        kind=node["kind"],
        name=node["name"],
        fqn=node["fqn"],
        symbol=node["symbol"],
        file=node.get("file"),
        start_line=node.get("start_line"),
        start_col=node.get("start_col"),
        end_line=node.get("end_line"),
        end_col=node.get("end_col"),
        documentation=node.get("documentation", []) or [],
        value_kind=node.get("value_kind"),
        type_symbol=node.get("type_symbol"),
        call_kind=node.get("call_kind"),
        signature=node.get("signature"),
        enclosing_start_line=node.get("enclosing_start_line"),
        enclosing_start_col=node.get("enclosing_start_col"),
        enclosing_end_line=node.get("enclosing_end_line"),
        enclosing_end_col=node.get("enclosing_end_col"),
    )


def records_to_nodes(records: list[Record], key: str = "n") -> list[NodeData]:
    """Convert a list of Neo4j Records to NodeData instances."""
    return [record_to_node(r, key) for r in records]
