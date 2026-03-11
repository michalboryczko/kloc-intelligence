"""Cypher queries for Property node context (USES and USED BY).

Pre-fetches all data needed to build Property context sections.
"""

from ..query_runner import QueryRunner

# =============================================================================
# Q1: Find All Calls Targeting a Property
# =============================================================================
# Find all Call nodes that target this Property via CALLS edges,
# grouped by containing method for access dedup.

Q1_PROPERTY_CALLS = """
MATCH (prop:Node {node_id: $property_id})<-[:CALLS]-(call:Call)
OPTIONAL MATCH (call)<-[:CONTAINS]-(scope)
WHERE scope.kind IN ['Method', 'Function']
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_prop)
OPTIONAL MATCH (recv_call)-[:RECEIVER]->(chain_recv:Value)
OPTIONAL MATCH (call)-[:PRODUCES]->(result:Value)
RETURN call.node_id AS call_id,
       call.file AS call_file,
       call.start_line AS call_line,
       call.call_kind AS call_kind,
       scope.node_id AS scope_id,
       scope.fqn AS scope_fqn,
       scope.kind AS scope_kind,
       scope.signature AS scope_signature,
       recv.node_id AS recv_id,
       recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       recv_prop.fqn AS recv_prop_fqn,
       recv_prop.name AS recv_prop_name,
       chain_recv.name AS chain_recv_name,
       chain_recv.value_kind AS chain_recv_kind,
       result.node_id AS result_id
ORDER BY call.node_id
"""

# =============================================================================
# Q2: Find Value Nodes Associated with a Property (promoted parameter)
# =============================================================================
# Check if the property has an assigned_from edge to a Value(parameter).

Q2_PROMOTED_PARAMETER = """
MATCH (prop:Node {node_id: $property_id})-[:ASSIGNED_FROM]->(param:Value {value_kind: 'parameter'})
RETURN param.node_id AS param_id,
       param.fqn AS param_fqn,
       param.name AS param_name,
       param.file AS param_file,
       param.start_line AS param_line,
       param.value_kind AS param_value_kind
"""

# =============================================================================
# Q3: Find Argument Edges Matching a Parameter FQN
# =============================================================================
# For property USES: find callers that pass values for this parameter.

Q3_PARAM_CALLERS = """
MATCH (call:Call)-[a:ARGUMENT]->(val:Value)
WHERE a.parameter = $param_fqn
OPTIONAL MATCH (call)<-[:CONTAINS]-(scope)
WHERE scope.kind IN ['Method', 'Function']
OPTIONAL MATCH (val)-[:TYPE_OF]->(vtype)
RETURN call.node_id AS call_id,
       call.file AS call_file,
       call.start_line AS call_line,
       val.node_id AS value_id,
       val.value_kind AS value_kind,
       val.name AS value_name,
       val.fqn AS value_fqn,
       scope.node_id AS scope_id,
       scope.fqn AS scope_fqn,
       scope.kind AS scope_kind,
       scope.signature AS scope_signature,
       a.position AS position,
       a.expression AS expression,
       vtype.name AS value_type
ORDER BY call.file, call.start_line
"""


def fetch_property_used_by_data(runner: QueryRunner, property_id: str) -> dict:
    """Batch-fetch all data needed to build the USED BY section for a Property.

    Runs Q1 and returns call records grouped by scope.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        property_id: The node_id of the target Property node.

    Returns:
        Dict with keys:
            "calls": list of Q1 records (dicts)
    """
    q1_records = runner.execute(Q1_PROPERTY_CALLS, property_id=property_id)
    return {
        "calls": [dict(r) for r in q1_records],
    }


def fetch_property_uses_data(runner: QueryRunner, property_id: str) -> dict:
    """Fetch data for building USES section of a Property.

    Checks for promoted parameter (assigned_from edge).

    Args:
        runner: Active QueryRunner.
        property_id: The node_id of the Property node.

    Returns:
        Dict with keys:
            "promoted_params": list of Q2 records (dicts)
    """
    q2_records = runner.execute(Q2_PROMOTED_PARAMETER, property_id=property_id)
    return {
        "promoted_params": [dict(r) for r in q2_records],
    }
