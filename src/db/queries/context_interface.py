"""Cypher queries for Interface node USED BY and USES context.

Pre-fetches all data needed to build interface context responses. Designed
to minimise round-trips: batch functions fetch everything, then pure-logic
orchestration assembles the result.
"""

from ..query_runner import QueryRunner

# =============================================================================
# Q1: Direct Implementors
# =============================================================================

Q1_DIRECT_IMPLEMENTORS = """
MATCH (iface:Node {node_id: $id})<-[:IMPLEMENTS]-(impl)
RETURN impl.node_id AS id, impl.fqn AS fqn, impl.kind AS kind,
       impl.file AS file, impl.start_line AS start_line
ORDER BY impl.file, impl.start_line
"""

# =============================================================================
# Q2: Extends Children (child interfaces)
# =============================================================================

Q2_EXTENDS_CHILDREN = """
MATCH (iface:Node {node_id: $id})<-[:EXTENDS]-(child)
WHERE child.kind = 'Interface'
RETURN child.node_id AS id, child.fqn AS fqn, child.kind AS kind,
       child.file AS file, child.start_line AS start_line
ORDER BY child.file, child.start_line
"""

# =============================================================================
# Q3: Property Type Injection Points (direct)
# =============================================================================

Q3_INJECTION_POINTS = """
MATCH (iface:Node {node_id: $id})<-[:TYPE_HINT]-(prop:Property)
MATCH (prop)<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN DISTINCT prop.node_id AS prop_id, prop.fqn AS prop_fqn,
       prop.file AS prop_file, prop.start_line AS prop_start_line,
       cls.node_id AS class_id, cls.fqn AS class_fqn,
       iface.fqn AS via_fqn

UNION

MATCH (iface:Node {node_id: $id})<-[:IMPLEMENTS]-(impl)<-[:TYPE_HINT]-(prop:Property)
MATCH (prop)<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN DISTINCT prop.node_id AS prop_id, prop.fqn AS prop_fqn,
       prop.file AS prop_file, prop.start_line AS prop_start_line,
       cls.node_id AS class_id, cls.fqn AS class_fqn,
       impl.fqn AS via_fqn

UNION

MATCH (iface:Node {node_id: $id})<-[:EXTENDS*]-(child_iface:Interface)<-[:TYPE_HINT]-(prop:Property)
MATCH (prop)<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN DISTINCT prop.node_id AS prop_id, prop.fqn AS prop_fqn,
       prop.file AS prop_file, prop.start_line AS prop_start_line,
       cls.node_id AS class_id, cls.fqn AS class_fqn,
       child_iface.fqn AS via_fqn

UNION

MATCH (iface:Node {node_id: $id})<-[:EXTENDS*]-(child_iface:Interface)<-[:IMPLEMENTS]-(impl)<-[:TYPE_HINT]-(prop:Property)
MATCH (prop)<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN DISTINCT prop.node_id AS prop_id, prop.fqn AS prop_fqn,
       prop.file AS prop_file, prop.start_line AS prop_start_line,
       cls.node_id AS class_id, cls.fqn AS class_fqn,
       impl.fqn AS via_fqn
"""

# =============================================================================
# Q4: Contract Method Names
# =============================================================================

Q4_CONTRACT_METHOD_NAMES = """
MATCH (iface:Node {node_id: $id})-[:CONTAINS]->(method:Method)
RETURN method.name AS method_name
"""

# =============================================================================
# Q5: Contract Relevance Check (for a specific property)
# =============================================================================

Q5_CONTRACT_RELEVANCE = """
MATCH (prop:Node {node_id: $prop_id})<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
MATCH (cls)-[:CONTAINS*]->(call:Call)
MATCH (call)-[:RECEIVER]->(recv:Value)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_target)
WHERE recv_target.fqn = $prop_fqn
MATCH (call)-[:CALLS]->(callee)
WHERE callee.name IN $contract_method_names
RETURN count(*) > 0 AS calls_contract
"""

# =============================================================================
# Q6: Injection Point Calls
# =============================================================================

Q6_INJECTION_POINT_CALLS = """
MATCH (prop:Node {node_id: $property_id})<-[:CONTAINS*]-(cls)
WHERE cls.kind IN ['Class', 'Interface', 'Trait', 'Enum']
MATCH (cls)-[:CONTAINS*]->(call:Call)
MATCH (call)-[:RECEIVER]->(recv:Value)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_target)
WHERE recv_target.fqn = $property_fqn
MATCH (call)-[:CALLS]->(callee)
OPTIONAL MATCH (call)<-[:CONTAINS]-(method:Method)
RETURN COALESCE(method.node_id, cls.node_id) AS method_id,
       COALESCE(method.fqn, cls.fqn) AS method_fqn,
       COALESCE(method.name, cls.name) AS method_name,
       COALESCE(method.file, cls.file) AS method_file,
       call.node_id AS call_id, call.call_kind AS call_kind,
       call.start_line AS call_line,
       callee.node_id AS callee_id, callee.fqn AS callee_fqn,
       callee.name AS callee_name, callee.kind AS callee_kind,
       cls.node_id AS class_id, cls.fqn AS class_fqn
ORDER BY COALESCE(method.node_id, cls.node_id), call.start_line
"""

# =============================================================================
# Q7: Interface Methods (own methods for depth-2 extends)
# =============================================================================

Q7_INTERFACE_METHODS = """
MATCH (iface:Node {node_id: $id})-[:CONTAINS]->(method:Method)
RETURN method.node_id AS id, method.fqn AS fqn, method.name AS name,
       method.file AS file, method.start_line AS start_line,
       method.signature AS signature
ORDER BY method.start_line
"""

# =============================================================================
# Q8: Implements Depth-2 (override methods in implementing class)
# =============================================================================

Q8_IMPLEMENTS_DEPTH2 = """
MATCH (impl:Node {node_id: $impl_id})-[:CONTAINS]->(method:Method)
OPTIONAL MATCH (method)-[:OVERRIDES]->(parent_method)
OPTIONAL MATCH (parent_method)<-[:CONTAINS]-(parent_cls)
RETURN method.node_id AS method_id, method.fqn AS method_fqn,
       method.name AS method_name, method.file AS method_file,
       method.start_line AS method_start_line,
       method.signature AS method_signature,
       parent_method.node_id AS overrides_id,
       parent_cls.node_id AS overrides_class_id
ORDER BY method.start_line
"""

# =============================================================================
# Q9: Interface Signature Types (for USES)
# =============================================================================

Q9_SIGNATURE_TYPES = """
MATCH (iface:Node {node_id: $id})-[:CONTAINS]->(method:Method)
OPTIONAL MATCH (method)-[:TYPE_HINT]->(ret_type)
WHERE ret_type.node_id <> $id
WITH method, ret_type
OPTIONAL MATCH (method)-[:CONTAINS]->(arg:Argument)-[:TYPE_HINT]->(param_type)
WHERE param_type.node_id <> $id
RETURN method.node_id AS method_id,
       method.file AS method_file,
       method.start_line AS method_line,
       ret_type.node_id AS ret_type_id,
       ret_type.fqn AS ret_type_fqn,
       ret_type.kind AS ret_type_kind,
       param_type.node_id AS param_type_id,
       param_type.fqn AS param_type_fqn,
       param_type.kind AS param_type_kind
"""

# =============================================================================
# Q10: Interface Extends Parent (for USES)
# =============================================================================

Q10_EXTENDS_PARENT = """
MATCH (iface:Node {node_id: $id})-[:EXTENDS]->(parent)
RETURN parent.node_id AS id, parent.fqn AS fqn, parent.kind AS kind,
       parent.file AS file, parent.start_line AS start_line
"""


def fetch_interface_used_by_data(runner: QueryRunner, node_id: str) -> dict:
    """Batch-fetch all data needed to build the USED BY section for an Interface.

    Runs Q1, Q2, Q3, Q4 in sequence and returns a dict. Q5, Q6, Q8 are
    invoked per-item by the orchestration layer.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node_id: The node_id of the target Interface node.

    Returns:
        Dict with keys:
            "implementors":       list of Q1 records (dicts)
            "extends_children":   list of Q2 records (dicts)
            "injection_points":   list of Q3 records (dicts)
            "contract_methods":   list of method name strings from Q4
    """
    q1_records = runner.execute(Q1_DIRECT_IMPLEMENTORS, id=node_id)
    q2_records = runner.execute(Q2_EXTENDS_CHILDREN, id=node_id)
    q3_records = runner.execute(Q3_INJECTION_POINTS, id=node_id)
    q4_records = runner.execute(Q4_CONTRACT_METHOD_NAMES, id=node_id)

    implementors = [dict(r) for r in q1_records]
    extends_children = [dict(r) for r in q2_records]
    injection_points = [dict(r) for r in q3_records]
    contract_methods = [r["method_name"] for r in q4_records if r["method_name"]]

    return {
        "implementors": implementors,
        "extends_children": extends_children,
        "injection_points": injection_points,
        "contract_methods": contract_methods,
    }


def fetch_interface_uses_data(runner: QueryRunner, node_id: str) -> dict:
    """Batch-fetch all data needed to build the USES section for an Interface.

    Runs Q9 and Q10 and returns a dict.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node_id: The node_id of the target Interface node.

    Returns:
        Dict with keys:
            "signature_types":  list of Q9 records (dicts)
            "extends_parent":   list of Q10 records (dicts)
    """
    q9_records = runner.execute(Q9_SIGNATURE_TYPES, id=node_id)
    q10_records = runner.execute(Q10_EXTENDS_PARENT, id=node_id)

    return {
        "signature_types": [dict(r) for r in q9_records],
        "extends_parent": [dict(r) for r in q10_records],
    }
