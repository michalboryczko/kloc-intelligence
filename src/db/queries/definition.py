"""Cypher queries for fetching definition data.

Provides named queries and a fetch_definition_data() function that runs them
all and returns a dict suitable for build_definition().
"""

from ..query_runner import QueryRunner

# Main definition query: node and its parent (containment)
DEFINITION_NODE = """
MATCH (n:Node {node_id: $node_id})
OPTIONAL MATCH (n)<-[:CONTAINS]-(parent)
RETURN n, parent
"""

# Children of the node ordered by sot.json insertion order (ordinal on CONTAINS edge)
DEFINITION_CHILDREN = """
MATCH (n:Node {node_id: $node_id})-[r:CONTAINS]->(child)
RETURN child ORDER BY r.ordinal
"""

# Type hints for each child of the node
DEFINITION_CHILD_TYPE_HINTS = """
MATCH (n:Node {node_id: $node_id})-[:CONTAINS]->(child)
OPTIONAL MATCH (child)-[:TYPE_HINT]->(th_target)
RETURN child.node_id AS child_id, collect(th_target) AS type_hints
"""

# Type hints on the node itself
DEFINITION_TYPE_HINTS = """
MATCH (n:Node {node_id: $node_id})-[:TYPE_HINT]->(th_target)
RETURN th_target
"""

# Inheritance: extends, implements, uses_trait
DEFINITION_INHERITANCE = """
MATCH (n:Node {node_id: $node_id})
OPTIONAL MATCH (n)-[:EXTENDS]->(parent_class)
OPTIONAL MATCH (n)-[:IMPLEMENTS]->(iface)
OPTIONAL MATCH (n)-[:USES_TRAIT]->(trait)
RETURN parent_class.fqn AS extends_fqn,
       collect(DISTINCT iface.fqn) AS implements_fqns,
       collect(DISTINCT trait.fqn) AS uses_trait_fqns
"""

# Check if a child method overrides a parent method
DEFINITION_OVERRIDES = """
MATCH (child:Node {node_id: $child_id})-[:OVERRIDES]->(parent)
RETURN parent.node_id AS parent_id
LIMIT 1
"""

# Promoted properties: properties assigned from Value(parameter) in __construct
DEFINITION_PROMOTED_PROPERTIES = """
MATCH (n:Node {node_id: $node_id})-[:CONTAINS]->(prop:Node {kind: 'Property'})
MATCH (prop)-[:ASSIGNED_FROM]->(v:Node {kind: 'Value', value_kind: 'parameter'})
WHERE v.fqn CONTAINS '__construct()'
RETURN prop.node_id AS prop_id
"""

# Value source: assigned_from chain for data flow
DEFINITION_VALUE_SOURCE = """
MATCH (v:Node {node_id: $node_id})
OPTIONAL MATCH (v)-[:ASSIGNED_FROM]->(af)
OPTIONAL MATCH (af)<-[:PRODUCES]-(call:Call)-[:CALLS]->(callee)
RETURN af, call, callee
"""

# Result source: for result values, source is the producing Call
DEFINITION_RESULT_SOURCE = """
MATCH (v:Node {node_id: $node_id})
OPTIONAL MATCH (v)<-[:PRODUCES]-(call:Call)-[:CALLS]->(callee)
RETURN call, callee
"""

# Type-of edges for Value nodes
DEFINITION_TYPE_OF = """
MATCH (v:Node {node_id: $node_id})-[:TYPE_OF]->(vtype)
RETURN vtype
"""

# Scope: walk up containment to find containing Method/Function
DEFINITION_SCOPE = """
MATCH (n:Node {node_id: $node_id})<-[:CONTAINS*]-(scope)
WHERE scope.kind IN ['Method', 'Function']
RETURN scope
LIMIT 1
"""


def _node_to_dict(neo4j_node) -> dict:
    """Convert a Neo4j node to a plain dict."""
    if neo4j_node is None:
        return {}
    return dict(neo4j_node)


def fetch_definition_data(runner: QueryRunner, node_id: str) -> dict:
    """Fetch all definition data for a node in batched queries.

    Returns a dict suitable for passing to build_definition().
    """
    # 1. Fetch node and parent
    record = runner.execute_single(DEFINITION_NODE, node_id=node_id)
    if not record:
        return {"node": None}

    node_data = _node_to_dict(record["n"])
    parent_data = _node_to_dict(record["parent"]) if record["parent"] else None

    kind = node_data.get("kind", "")

    # 2. Fetch children (for Method, Function, Class, Interface, Trait, Enum)
    children = []
    child_type_hints: dict[str, list[dict]] = {}
    overrides: dict[str, str | None] = {}
    promoted_properties: set[str] = set()

    if kind in ("Method", "Function", "Class", "Interface", "Trait", "Enum"):
        child_records = runner.execute(DEFINITION_CHILDREN, node_id=node_id)
        children = [_node_to_dict(r["child"]) for r in child_records]

        # Child type hints
        cth_records = runner.execute(DEFINITION_CHILD_TYPE_HINTS, node_id=node_id)
        for r in cth_records:
            cid = r["child_id"]
            hints = [_node_to_dict(th) for th in (r["type_hints"] or []) if th is not None]
            if hints:
                child_type_hints[cid] = hints

        # Overrides (only for Class/Trait/Enum methods)
        if kind in ("Class", "Trait", "Enum"):
            for child in children:
                if child.get("kind") == "Method":
                    cid = child.get("node_id")
                    if cid:
                        ov_record = runner.execute_single(DEFINITION_OVERRIDES, child_id=cid)
                        if ov_record and ov_record["parent_id"]:
                            overrides[cid] = ov_record["parent_id"]

            # Promoted properties
            promo_records = runner.execute(DEFINITION_PROMOTED_PROPERTIES, node_id=node_id)
            for r in promo_records:
                promoted_properties.add(r["prop_id"])

    # 3. Type hints on the node itself (for Method, Function, Property, Argument)
    type_hints: list[dict] = []
    if kind in ("Method", "Function", "Property", "Argument"):
        th_records = runner.execute(DEFINITION_TYPE_HINTS, node_id=node_id)
        type_hints = [_node_to_dict(r["th_target"]) for r in th_records]

    # 4. Inheritance (for Class, Interface, Trait, Enum)
    inheritance: dict = {}
    if kind in ("Class", "Interface", "Trait", "Enum"):
        inh_record = runner.execute_single(DEFINITION_INHERITANCE, node_id=node_id)
        if inh_record:
            inheritance = {
                "extends_fqn": inh_record["extends_fqn"],
                "implements_fqns": [f for f in (inh_record["implements_fqns"] or []) if f],
                "uses_trait_fqns": [f for f in (inh_record["uses_trait_fqns"] or []) if f],
            }

    # 5. Value-specific data
    value_source: dict = {}
    result_source: dict = {}
    type_of: list[dict] = []
    scope: dict | None = None

    if kind == "Value":
        # Type-of
        to_records = runner.execute(DEFINITION_TYPE_OF, node_id=node_id)
        type_of = [_node_to_dict(r["vtype"]) for r in to_records]

        # Value source
        vs_record = runner.execute_single(DEFINITION_VALUE_SOURCE, node_id=node_id)
        if vs_record:
            af = _node_to_dict(vs_record["af"]) if vs_record["af"] else None
            call = _node_to_dict(vs_record["call"]) if vs_record["call"] else None
            callee = _node_to_dict(vs_record["callee"]) if vs_record["callee"] else None
            if af or call:
                value_source = {"af": af, "call": call, "callee": callee}

        # Result source
        if node_data.get("value_kind") == "result" and not value_source:
            rs_record = runner.execute_single(DEFINITION_RESULT_SOURCE, node_id=node_id)
            if rs_record:
                call = _node_to_dict(rs_record["call"]) if rs_record["call"] else None
                callee = _node_to_dict(rs_record["callee"]) if rs_record["callee"] else None
                if call:
                    result_source = {"call": call, "callee": callee}

        # Scope
        scope_record = runner.execute_single(DEFINITION_SCOPE, node_id=node_id)
        if scope_record and scope_record["scope"]:
            scope = _node_to_dict(scope_record["scope"])

    # 6. Property promoted check
    if kind == "Property":
        promo_records = runner.execute(
            DEFINITION_PROMOTED_PROPERTIES,
            node_id=parent_data.get("node_id", "") if parent_data else "",
        )
        for r in promo_records:
            if r["prop_id"] == node_id:
                promoted_properties.add(node_id)

    return {
        "node": node_data,
        "parent": parent_data,
        "children": children,
        "child_type_hints": child_type_hints,
        "type_hints": type_hints,
        "inheritance": inheritance,
        "overrides": overrides,
        "promoted_properties": promoted_properties,
        "value_source": value_source,
        "result_source": result_source,
        "type_of": type_of,
        "scope": scope,
    }
