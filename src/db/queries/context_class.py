"""Cypher queries for Class node USED BY context.

Pre-fetches all data needed to build the USED BY section of a class context
response. Designed to minimise round-trips: one batch call fetches everything,
then pure-logic orchestration assembles the result.
"""

from ..query_runner import QueryRunner

# =============================================================================
# Q1: Extends/Implements Children
# =============================================================================
# Direct classes/interfaces that extend or implement the target.

Q1_EXTENDS_IMPLEMENTS_CHILDREN = """
MATCH (target:Node {node_id: $id})<-[r:EXTENDS|IMPLEMENTS]-(child)
RETURN child.node_id AS id, child.fqn AS fqn, child.kind AS kind,
       child.file AS file, child.start_line AS start_line,
       type(r) AS rel_type
ORDER BY child.file, child.start_line
"""

# =============================================================================
# Q2: All Incoming Usages with Context
# =============================================================================
# All incoming usage edges with source node data, nearest containing class,
# and nearest containing method (pre-resolved to avoid per-edge round-trips).

Q2_INCOMING_USAGES = """
MATCH (target:Node {node_id: $id})<-[e]-(source:Node)
WHERE type(e) IN ['USES', 'EXTENDS', 'IMPLEMENTS', 'USES_TRAIT']
  AND source.kind <> 'File'
  AND NOT EXISTS { MATCH (source)<-[:CONTAINS*]-(target) }
OPTIONAL MATCH path = (source)<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
WITH source, e, cls, length(path) AS path_len
ORDER BY path_len ASC
WITH source, e, COLLECT(cls)[0] AS containing_class
OPTIONAL MATCH method_path = (source)<-[:CONTAINS*]-(method)
WHERE method.kind IN ['Method', 'Function']
WITH source, e, containing_class, method, length(method_path) AS method_path_len
ORDER BY method_path_len ASC
WITH source, e, containing_class, COLLECT(method)[0] AS containing_method
RETURN source.node_id AS source_id,
       source.fqn AS source_fqn,
       source.kind AS source_kind,
       source.name AS source_name,
       source.file AS source_file,
       source.start_line AS source_start_line,
       source.signature AS source_signature,
       type(e) AS edge_type,
       e.target AS edge_target,
       e.loc_file AS edge_file,
       e.loc_line AS edge_line,
       containing_class.node_id AS containing_class_id,
       containing_class.fqn AS containing_class_fqn,
       containing_class.kind AS containing_class_kind,
       containing_class.file AS containing_class_file,
       containing_class.start_line AS containing_class_start_line,
       containing_method.node_id AS containing_method_id,
       containing_method.fqn AS containing_method_fqn,
       containing_method.kind AS containing_method_kind
"""

# =============================================================================
# Q3: Injection Detection
# =============================================================================
# Find classes that have a property_type injection for the target class.
# Used to suppress redundant method_call entries at depth 1.

Q3_INJECTION_DETECTION = """
MATCH (target:Node {node_id: $id})<-[:USES]-(source)
WHERE source.kind = 'Property'
  AND EXISTS { (source)-[:TYPE_HINT]->(target) }
MATCH (source)<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN DISTINCT cls.node_id AS class_with_injection

UNION

MATCH (target:Node {node_id: $id})<-[:USES]-(source)
WHERE source.kind IN ['Method', 'Function']
  AND source.name = '__construct'
MATCH (source)<-[:CONTAINS]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
MATCH (cls)-[:CONTAINS]->(prop:Property)-[:TYPE_HINT]->(target)
RETURN DISTINCT cls.node_id AS class_with_injection
"""

# =============================================================================
# Q4: Call Node Resolution
# =============================================================================
# For each source that uses the target, find associated Call nodes with their
# targets and receiver chains.

Q4_CALL_NODE_RESOLUTION = """
MATCH (target:Node {node_id: $id})<-[:USES]-(source)
WHERE source.kind <> 'File'
MATCH (source)-[:CONTAINS]->(call:Call)
OPTIONAL MATCH (call)-[:CALLS]->(callee)
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_target:Property)
RETURN source.node_id AS source_id,
       call.node_id AS call_id,
       call.call_kind AS call_kind,
       call.file AS call_file,
       call.start_line AS call_start_line,
       callee.node_id AS callee_id,
       callee.fqn AS callee_fqn,
       recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       recv_target.fqn AS access_chain_symbol
"""

# =============================================================================
# Q5: Caller Chain
# =============================================================================
# Find callers of a method for depth 2+ expansion.

Q5_CALLER_CHAIN = """
MATCH (method:Node {node_id: $method_id})<-[:CALLS]-(call:Call)<-[:CONTAINS]-(caller)
WHERE caller.kind IN ['Method', 'Function']
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_prop:Property)
RETURN caller.node_id AS caller_id,
       caller.fqn AS caller_fqn,
       caller.kind AS caller_kind,
       caller.file AS caller_file,
       caller.start_line AS caller_start_line,
       call.node_id AS call_id,
       call.start_line AS call_line,
       recv_prop.fqn AS on_property
ORDER BY caller.file, call.start_line
"""

# =============================================================================
# Q6: Property Type Resolution
# =============================================================================
# Resolve property nodes that have a type_hint edge pointing to the target.

Q6_PROPERTY_TYPE_RESOLUTION = """
MATCH (target:Node {node_id: $id})<-[:TYPE_HINT]-(prop:Property)
RETURN prop.node_id AS prop_id, prop.fqn AS prop_fqn,
       prop.file AS prop_file, prop.start_line AS prop_start_line
"""

# =============================================================================
# Q7: Injection Point Calls
# =============================================================================
# Find calls made through an injected property (for depth-2 under property_type).

Q7_INJECTION_POINT_CALLS = """
MATCH (prop:Node {node_id: $property_id})<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
MATCH (cls)-[:CONTAINS]->(method:Method)-[:CONTAINS]->(call:Call)
MATCH (call)-[:RECEIVER]->(recv:Value)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_target)
WHERE recv_target.fqn = $property_fqn
MATCH (call)-[:CALLS]->(callee)
RETURN method.node_id AS method_id, method.fqn AS method_fqn,
       call.node_id AS call_id, call.call_kind AS call_kind,
       call.start_line AS call_line,
       callee.node_id AS callee_id, callee.fqn AS callee_fqn,
       callee.name AS callee_name, callee.kind AS callee_kind
ORDER BY method.fqn, call.start_line
"""

# =============================================================================
# Q8: Ref Type Data
# =============================================================================
# Pre-fetch type_hint classification data for each incoming edge.
# Mirrors helpers.REF_TYPE_DATA but scoped to a single target.

Q8_REF_TYPE_DATA = """
MATCH (target:Node {node_id: $target_id})<-[e]-(source:Node)
WHERE type(e) IN ['USES', 'EXTENDS', 'IMPLEMENTS', 'USES_TRAIT']
  AND source.kind <> 'File'
WITH source, e, target
OPTIONAL MATCH (source)-[:CONTAINS]->(arg:Node {kind: 'Argument'})-[:TYPE_HINT]->(target)
WITH source, e, target, count(arg) > 0 AS has_arg_type_hint
OPTIONAL MATCH (source)-[rth:TYPE_HINT]->(target)
WHERE source.kind IN ['Method', 'Function']
WITH source, e, target, has_arg_type_hint, count(rth) > 0 AS has_return_type_hint
OPTIONAL MATCH (source)<-[:CONTAINS]-(parent_cls:Node)-[:CONTAINS]->(prop:Node {kind: 'Property'})-[:TYPE_HINT]->(target)
WHERE source.kind IN ['Method', 'Function'] AND source.name = '__construct'
WITH source, e, target, has_arg_type_hint, has_return_type_hint,
     count(prop) > 0 AS has_class_property_type_hint
OPTIONAL MATCH (source)-[:CONTAINS]->(src_prop:Node {kind: 'Property'})-[:TYPE_HINT]->(target)
WHERE source.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN source.node_id AS source_id,
       has_arg_type_hint,
       has_return_type_hint,
       has_class_property_type_hint,
       count(src_prop) > 0 AS has_source_class_property_type_hint
"""


# =============================================================================
# Q9: Constructor Instantiations
# =============================================================================
# Find calls that construct this class (call_kind=constructor, calls -> __construct).
Q9_CONSTRUCTOR_CALLS = """
MATCH (cls:Node {node_id: $id})-[:CONTAINS]->(ctor:Method {name: '__construct'})
MATCH (call:Call {call_kind: 'constructor'})-[:CALLS]->(ctor)
MATCH (call)<-[:CONTAINS]-(method)
WHERE method.kind IN ['Method', 'Function']
MATCH (method)<-[:CONTAINS*]-(method_cls)
WHERE method_cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
  AND method_cls.node_id <> $id
OPTIONAL MATCH (method)-[e:USES]->(cls)
RETURN method.node_id AS method_id,
       method.fqn AS method_fqn,
       method.kind AS method_kind,
       method.file AS method_file,
       call.node_id AS call_id,
       call.start_line AS call_line,
       call.file AS call_file,
       e.loc_line AS edge_line,
       e.loc_file AS edge_file
ORDER BY method.file, call.start_line
"""

# =============================================================================
# Q10: External Method Calls
# =============================================================================
# Find external methods that call methods of this class.
Q10_EXTERNAL_METHOD_CALLS = """
MATCH (cls:Node {node_id: $id})-[:CONTAINS]->(member:Method)
WHERE member.name <> '__construct'
MATCH (call:Call)-[:CALLS]->(member)
MATCH (call)<-[:CONTAINS]-(caller)
WHERE caller.kind IN ['Method', 'Function']
  AND NOT (caller)<-[:CONTAINS*]-(cls)
MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_prop)
OPTIONAL MATCH (recv_call)-[:RECEIVER]->(src_recv:Value)
OPTIONAL MATCH (caller)-[e:USES]->(member)
OPTIONAL MATCH (caller)<-[:CONTAINS]-(caller_class)
WHERE caller_class.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN caller.node_id AS caller_id,
       caller.fqn AS caller_fqn,
       caller.kind AS caller_kind,
       caller.file AS caller_file,
       caller_class.node_id AS caller_class_id,
       call.node_id AS call_id,
       call.start_line AS call_line,
       call.file AS call_file,
       member.name AS callee_name,
       member.kind AS callee_kind,
       recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       recv_prop.fqn AS recv_prop_fqn,
       recv_prop.name AS recv_prop_name,
       src_recv.value_kind AS src_recv_value_kind,
       src_recv.name AS src_recv_name,
       e.loc_line AS edge_line,
       e.loc_file AS edge_file
ORDER BY caller.file, call.start_line
"""

# =============================================================================
# Q11: External Property Accesses
# =============================================================================
# Find external methods that access properties of this class.
Q11_EXTERNAL_PROPERTY_ACCESSES = """
MATCH (cls:Node {node_id: $id})-[:CONTAINS]->(prop:Property)
MATCH (caller)-[e:USES]->(prop)
WHERE caller.kind IN ['Method', 'Function']
  AND NOT (caller)<-[:CONTAINS*]-(cls)
OPTIONAL MATCH (caller)-[:CONTAINS]->(call:Call)-[:CALLS]->(prop)
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(src_call:Call)-[:CALLS]->(src_prop)
OPTIONAL MATCH (src_call)-[:RECEIVER]->(src_recv:Value)
WITH caller, e, prop, call, recv, src_prop, src_recv,
     CASE WHEN call IS NOT NULL AND call.start_line = e.loc_line
          THEN 0
          ELSE 1
     END AS exact_match,
     call.node_id AS call_nid
ORDER BY exact_match ASC, call_nid ASC
WITH caller, e, prop,
     COLLECT(recv.name)[0] AS recv_name,
     COLLECT(recv.value_kind)[0] AS recv_value_kind,
     COLLECT(src_prop.name)[0] AS src_prop_name,
     COLLECT(src_prop.fqn)[0] AS src_prop_fqn,
     COLLECT(src_recv.value_kind)[0] AS src_recv_value_kind,
     COLLECT(src_recv.name)[0] AS src_recv_name
RETURN caller.node_id AS caller_id,
       caller.fqn AS caller_fqn,
       caller.kind AS caller_kind,
       caller.file AS caller_file,
       prop.fqn AS prop_fqn,
       prop.name AS prop_name,
       e.loc_line AS call_line,
       e.loc_file AS call_file,
       recv_name,
       recv_value_kind,
       src_prop_name,
       src_prop_fqn,
       src_recv_value_kind,
       src_recv_name
ORDER BY prop.fqn, caller.file, e.loc_line
"""


def fetch_class_used_by_data(runner: QueryRunner, node_id: str) -> dict:
    """Batch-fetch all data needed to build the USED BY section for a Class.

    Runs Q1, Q2, Q3, Q4, Q6, and Q8 in sequence and returns a dict keyed by
    query name. The orchestration layer consumes this dict without needing
    additional Neo4j calls.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node_id: The node_id of the target Class/Interface/Trait/Enum.

    Returns:
        Dict with keys:
            "extends_children": list of Q1 records (dicts)
            "incoming_usages":  list of Q2 records (dicts)
            "injected_classes": set of class node_ids from Q3
            "call_nodes":       list of Q4 records (dicts)
            "property_types":   list of Q6 records (dicts)
            "ref_type_data":    dict keyed by source_id from Q8
    """
    q1_records = runner.execute(Q1_EXTENDS_IMPLEMENTS_CHILDREN, id=node_id)
    q2_records = runner.execute(Q2_INCOMING_USAGES, id=node_id)
    q3_records = runner.execute(Q3_INJECTION_DETECTION, id=node_id)
    q4_records = runner.execute(Q4_CALL_NODE_RESOLUTION, id=node_id)
    q6_records = runner.execute(Q6_PROPERTY_TYPE_RESOLUTION, id=node_id)
    q8_records = runner.execute(Q8_REF_TYPE_DATA, target_id=node_id)
    q9_records = runner.execute(Q9_CONSTRUCTOR_CALLS, id=node_id)
    q10_records = runner.execute(Q10_EXTERNAL_METHOD_CALLS, id=node_id)
    q11_records = runner.execute(Q11_EXTERNAL_PROPERTY_ACCESSES, id=node_id)

    # Materialise neo4j Records into plain dicts for easier downstream use
    extends_children = [dict(r) for r in q1_records]
    incoming_usages = [dict(r) for r in q2_records]
    injected_classes = {r["class_with_injection"] for r in q3_records if r["class_with_injection"]}
    call_nodes = [dict(r) for r in q4_records]
    property_types = [dict(r) for r in q6_records]
    constructor_calls = [dict(r) for r in q9_records]
    external_method_calls = [dict(r) for r in q10_records]
    external_property_accesses = [dict(r) for r in q11_records]

    # Index Q8 by source_id for O(1) lookup
    ref_type_data: dict[str, dict] = {}
    for r in q8_records:
        d = dict(r)
        ref_type_data[d["source_id"]] = d

    return {
        "extends_children": extends_children,
        "incoming_usages": incoming_usages,
        "injected_classes": injected_classes,
        "call_nodes": call_nodes,
        "property_types": property_types,
        "ref_type_data": ref_type_data,
        "constructor_calls": constructor_calls,
        "external_method_calls": external_method_calls,
        "external_property_accesses": external_property_accesses,
    }
