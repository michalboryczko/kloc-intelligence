"""Cypher queries for Class node USES context.

Pre-fetches all data needed to build the USES section of a class context
response (outgoing dependencies from the class).
"""

from ..query_runner import QueryRunner

# =============================================================================
# Q1: Member Dependencies
# =============================================================================
# All outgoing USES edges from the class's members (Methods + Properties)
# to external nodes, grouped with the containing member for display.

Q1_MEMBER_DEPENDENCIES = """
MATCH (cls:Node {node_id: $id})-[:CONTAINS]->(member)
WHERE member.kind IN ['Method', 'Property']
MATCH (member)-[e:USES]->(target:Node)
WHERE target.node_id <> $id
  AND target.kind IN ['Class', 'Interface', 'Trait', 'Enum', 'Method', 'Property', 'Function']
  AND NOT EXISTS { MATCH (cls)-[:CONTAINS*]->(target) }
RETURN member.node_id AS member_id,
       member.fqn AS member_fqn,
       member.kind AS member_kind,
       member.name AS member_name,
       target.node_id AS target_id,
       target.fqn AS target_fqn,
       target.kind AS target_kind,
       target.name AS target_name,
       type(e) AS edge_type,
       e.loc_file AS file,
       e.loc_line AS line
ORDER BY member.node_id, e.loc_line
"""

# =============================================================================
# Q2: Class-level Relationships
# =============================================================================
# Direct structural edges: extends, implements, uses_trait.

Q2_CLASS_LEVEL_RELATIONSHIPS = """
MATCH (cls:Node {node_id: $id})-[r]->(target)
WHERE type(r) IN ['EXTENDS', 'IMPLEMENTS', 'USES_TRAIT']
RETURN target.node_id AS target_id,
       target.fqn AS target_fqn,
       target.kind AS target_kind,
       type(r) AS rel_type,
       target.file AS file,
       target.start_line AS line
"""

# =============================================================================
# Q3: Behavioral Depth-2 (Method Execution Flow through injected property)
# =============================================================================
# Find methods called through a specific injected property.

# =============================================================================
# Q4: Type Hint Classification
# =============================================================================
# Pre-collect TYPE_HINT edges from class members (Property, Method, Argument)
# to classify targets accurately as property_type, return_type, parameter_type.

Q4_TYPE_HINT_CLASSIFICATION = """
MATCH (cls:Node {node_id: $id})-[:CONTAINS]->(member)
WHERE member.kind IN ['Method', 'Property']
OPTIONAL MATCH (member)-[:TYPE_HINT]->(th_target)
WHERE th_target.kind IN ['Class', 'Interface', 'Trait', 'Enum']
OPTIONAL MATCH (member)-[:CONTAINS]->(arg:Argument)-[:TYPE_HINT]->(arg_target)
WHERE arg_target.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN member.node_id AS member_id,
       member.kind AS member_kind,
       member.name AS member_name,
       member.file AS member_file,
       member.start_line AS member_line,
       th_target.node_id AS th_target_id,
       arg_target.node_id AS arg_target_id
"""

# =============================================================================
# Q5: Constructor Calls (Instantiation detection)
# =============================================================================
# Find constructor calls within this class's methods, resolving to the
# containing class of the constructor for instantiation detection.

Q5_CONSTRUCTOR_CALLS = """
MATCH (cls:Node {node_id: $id})-[:CONTAINS]->(method:Method)
MATCH (method)-[:CONTAINS]->(call:Call)-[:CALLS]->(ctor:Method {name: '__construct'})
MATCH (ctor)<-[:CONTAINS]-(target_cls)
WHERE target_cls.kind IN ['Class', 'Enum']
  AND target_cls.node_id <> $id
RETURN DISTINCT target_cls.node_id AS target_id,
       target_cls.fqn AS target_fqn,
       target_cls.kind AS target_kind,
       call.file AS call_file,
       call.start_line AS call_line
ORDER BY call.file, call.start_line
"""

# =============================================================================
# Q6: Containing Class Resolution for Targets
# =============================================================================
# For member-level targets (Method, Property), resolve to their containing class.

Q6_TARGET_CONTAINING_CLASS = """
MATCH (n:Node {node_id: $node_id})<-[:CONTAINS]-(parent)
WHERE parent.kind IN ['Class', 'Interface', 'Trait', 'Enum']
RETURN parent.node_id AS class_id,
       parent.fqn AS class_fqn,
       parent.kind AS class_kind,
       parent.file AS class_file,
       parent.start_line AS class_line
LIMIT 1
"""

Q3_BEHAVIORAL_DEPTH2 = """
MATCH (cls:Node {node_id: $class_id})-[:CONTAINS]->(method:Method)
MATCH (method)-[:CONTAINS]->(call:Call)-[:CALLS]->(callee)
MATCH (call)-[:RECEIVER]->(recv:Value)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(prop)
WHERE prop.fqn = $property_fqn
RETURN callee.node_id AS callee_id,
       callee.fqn AS callee_fqn,
       callee.kind AS callee_kind,
       callee.name AS callee_name,
       call.node_id AS call_id,
       call.file AS call_file,
       call.start_line AS call_line,
       method.fqn AS from_method
ORDER BY call.file, call.start_line
"""


def fetch_class_uses_data(runner: QueryRunner, node_id: str) -> dict:
    """Batch-fetch all data needed to build the USES section for a Class.

    Runs Q1, Q2, Q4, Q5 in sequence and returns a dict. Q3 is intentionally
    omitted here — it is invoked per-property by the orchestration layer when
    building depth-2 behavioral entries.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        node_id: The node_id of the target Class/Interface/Trait/Enum.

    Returns:
        Dict with keys:
            "member_deps":   list of Q1 records (dicts)
            "class_rel":     list of Q2 records (dicts)
            "type_hints":    list of Q4 records (dicts)
            "ctor_calls":    list of Q5 records (dicts)
    """
    q1_records = runner.execute(Q1_MEMBER_DEPENDENCIES, id=node_id)
    q2_records = runner.execute(Q2_CLASS_LEVEL_RELATIONSHIPS, id=node_id)
    q4_records = runner.execute(Q4_TYPE_HINT_CLASSIFICATION, id=node_id)
    q5_records = runner.execute(Q5_CONSTRUCTOR_CALLS, id=node_id)

    return {
        "member_deps": [dict(r) for r in q1_records],
        "class_rel": [dict(r) for r in q2_records],
        "type_hints": [dict(r) for r in q4_records],
        "ctor_calls": [dict(r) for r in q5_records],
    }
