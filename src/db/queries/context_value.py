"""Cypher queries for Value node context (consumer chain, source chain).

Pre-fetches all data needed to build consumer and source chains for Value nodes.
Batch functions minimise round-trips; orchestration assembles.
"""

from ..query_runner import QueryRunner

# =============================================================================
# Q1: Receiver Edges with Downstream Consumers
# =============================================================================
# For a given Value, find all Calls that use it as receiver, their targets,
# produced results, and whether those results are consumed as arguments by
# another Call.

Q1_RECEIVER_CHAIN = """
MATCH (v:Node {node_id: $value_id})<-[:RECEIVER]-(access_call:Call)
OPTIONAL MATCH (access_call)-[:CALLS]->(target)
OPTIONAL MATCH (access_call)-[:PRODUCES]->(result:Value)
OPTIONAL MATCH (result)<-[arg_edge:ARGUMENT]-(consumer_call:Call)
OPTIONAL MATCH (consumer_call)-[:CALLS]->(consumer_target)
OPTIONAL MATCH (result)<-[:ASSIGNED_FROM]-(assigned_local:Value {value_kind: 'local'})
RETURN access_call.node_id AS access_call_id,
       access_call.file AS access_call_file,
       access_call.start_line AS access_call_line,
       access_call.call_kind AS access_call_kind,
       target.node_id AS target_id,
       target.fqn AS target_fqn,
       target.name AS target_name,
       target.kind AS target_kind,
       result.node_id AS result_id,
       consumer_call.node_id AS consumer_call_id,
       consumer_call.file AS consumer_call_file,
       consumer_call.start_line AS consumer_call_line,
       consumer_call.call_kind AS consumer_call_kind,
       consumer_target.node_id AS consumer_target_id,
       consumer_target.fqn AS consumer_target_fqn,
       consumer_target.name AS consumer_target_name,
       consumer_target.kind AS consumer_target_kind,
       consumer_target.signature AS consumer_target_signature,
       arg_edge.position AS arg_position,
       arg_edge.expression AS arg_expression,
       assigned_local.node_id AS assigned_local_id
"""

# =============================================================================
# Q2: Direct Argument Edges
# =============================================================================
# Value passed directly as argument to a Call.

Q2_DIRECT_ARGUMENTS = """
MATCH (v:Node {node_id: $value_id})<-[:ARGUMENT]-(consumer_call:Call)
OPTIONAL MATCH (consumer_call)-[:CALLS]->(consumer_target)
RETURN consumer_call.node_id AS consumer_call_id,
       consumer_call.file AS consumer_call_file,
       consumer_call.start_line AS consumer_call_line,
       consumer_call.call_kind AS consumer_call_kind,
       consumer_target.node_id AS consumer_target_id,
       consumer_target.fqn AS consumer_target_fqn,
       consumer_target.name AS consumer_target_name,
       consumer_target.kind AS consumer_target_kind,
       consumer_target.signature AS consumer_target_signature
"""

# =============================================================================
# Q3: Receiver Identity for a Call
# =============================================================================
# Resolve receiver of a specific Call for access chain display.

Q3_RECEIVER_IDENTITY = """
MATCH (call:Node {node_id: $call_id})-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(src_call:Call)-[:CALLS]->(prop)
OPTIONAL MATCH (src_call)-[:RECEIVER]->(src_recv:Value)
RETURN recv.value_kind AS recv_kind,
       recv.name AS recv_name,
       recv.file AS recv_file,
       recv.start_line AS recv_start_line,
       src_call.call_kind AS src_call_kind,
       prop.fqn AS prop_fqn,
       prop.name AS prop_name,
       src_recv.value_kind AS src_recv_kind,
       src_recv.name AS src_recv_name
"""

# =============================================================================
# Q4: Argument Parameter FQN Resolution for a Call
# =============================================================================
# Get argument edges with their parameter FQNs for crossing into callee.

Q4_ARGUMENT_PARAMS = """
MATCH (call:Node {node_id: $call_id})-[a:ARGUMENT]->(val:Value)
WHERE a.parameter IS NOT NULL
RETURN a.parameter AS parameter_fqn,
       val.node_id AS value_id,
       a.position AS position,
       a.expression AS expression
ORDER BY a.position
"""

# =============================================================================
# Q5: Resolve Parameter FQN to Value Node
# =============================================================================
# Find Value(parameter) node matching a given FQN.

Q5_RESOLVE_PARAM = """
MATCH (v:Value {fqn: $param_fqn, value_kind: 'parameter'})
RETURN v.node_id AS value_id
LIMIT 1
"""

# =============================================================================
# Q6: Find Local Value for Call Result
# =============================================================================
# Check if a Call's produced result is assigned to a local variable.

Q6_LOCAL_FOR_RESULT = """
MATCH (call:Node {node_id: $call_id})-[:PRODUCES]->(result:Value)
OPTIONAL MATCH (local:Value {value_kind: 'local'})-[:ASSIGNED_FROM]->(result)
RETURN result.node_id AS result_id,
       local.node_id AS local_id
"""

# =============================================================================
# Q7: Type-of Resolution for a Value
# =============================================================================
# Get the type_of target node for a Value.

Q7_TYPE_OF = """
MATCH (v:Node {node_id: $value_id})-[:TYPE_OF]->(type_node)
RETURN type_node.node_id AS type_id,
       type_node.fqn AS type_fqn
LIMIT 1
"""

# =============================================================================
# Q8: Find Callers of a Method with Result/Local/Type Info
# =============================================================================
# For cross_into_callers_via_return: find calls to a method, their results,
# and local assignments with type matching.

Q8_METHOD_CALLERS = """
MATCH (method:Node {node_id: $method_id})<-[:CALLS]-(caller_call:Call)
OPTIONAL MATCH (caller_call)-[:PRODUCES]->(caller_result:Value)
OPTIONAL MATCH (caller_local:Value {value_kind: 'local'})-[:ASSIGNED_FROM]->(caller_result)
OPTIONAL MATCH (caller_local)-[:TYPE_OF]->(caller_type)
OPTIONAL MATCH (caller_call)<-[:CONTAINS]-(caller_method)
WHERE caller_method.kind IN ['Method', 'Function']
RETURN caller_call.node_id AS caller_call_id,
       caller_result.node_id AS caller_result_id,
       caller_local.node_id AS caller_local_id,
       caller_type.node_id AS caller_type_id,
       caller_method.node_id AS caller_method_id,
       caller_method.fqn AS caller_method_fqn,
       caller_method.kind AS caller_method_kind
"""

# =============================================================================
# Q9: Source Chain Traversal
# =============================================================================
# Follow assigned_from -> produces -> Call -> target for source chain.

Q9_SOURCE_CHAIN = """
MATCH (v:Node {node_id: $value_id})
OPTIONAL MATCH (v)-[:ASSIGNED_FROM]->(source:Value)
OPTIONAL MATCH (source)<-[:PRODUCES]-(call:Call)
OPTIONAL MATCH (call)-[:CALLS]->(callee)
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(recv_call:Call)-[:CALLS]->(recv_prop)
RETURN v.value_kind AS value_kind,
       v.fqn AS value_fqn,
       source.node_id AS source_id,
       source.value_kind AS source_kind,
       call.node_id AS call_id,
       call.file AS call_file,
       call.start_line AS call_line,
       call.call_kind AS call_kind,
       callee.node_id AS callee_id,
       callee.fqn AS callee_fqn,
       callee.name AS callee_name,
       callee.kind AS callee_kind,
       callee.signature AS callee_signature,
       recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       recv_prop.fqn AS recv_prop_fqn,
       recv_prop.name AS recv_prop_name
"""

# =============================================================================
# Q10: Parameter Uses (argument edges matching parameter FQN)
# =============================================================================
# Find all argument edges where the parameter field matches a given FQN.

Q10_PARAMETER_USES = """
MATCH (call:Call)-[a:ARGUMENT]->(val:Value)
WHERE a.parameter = $param_fqn
OPTIONAL MATCH (call)<-[:CONTAINS]-(scope)
WHERE scope.kind IN ['Method', 'Function']
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
       a.expression AS expression
ORDER BY call.file, call.start_line
"""

# =============================================================================
# Q11: Argument Info for a Call
# =============================================================================
# Get all argument-to-parameter mappings for a specific Call.

Q11_CALL_ARGUMENTS = """
MATCH (call:Node {node_id: $call_id})-[a:ARGUMENT]->(val:Value)
OPTIONAL MATCH (val)-[:TYPE_OF]->(vtype)
RETURN a.position AS position,
       a.expression AS expression,
       a.parameter AS parameter,
       val.node_id AS value_id,
       val.value_kind AS value_kind,
       val.name AS value_name,
       val.fqn AS value_fqn,
       vtype.name AS value_type
ORDER BY a.position
"""

# =============================================================================
# Q12: Containing Method of a Node
# =============================================================================

Q12_CONTAINING_METHOD = """
MATCH (n:Node {node_id: $node_id})
OPTIONAL MATCH path = (n)<-[:CONTAINS*1..10]-(ancestor)
WHERE ancestor.kind IN ['Method', 'Function']
WITH ancestor, path ORDER BY length(path) ASC LIMIT 1
RETURN ancestor.node_id AS method_id,
       ancestor.fqn AS method_fqn,
       ancestor.kind AS method_kind
"""


_Q_VALUE_IDENTITY = """
MATCH (v:Node {node_id: $value_id})
RETURN v.name AS name, v.value_kind AS value_kind
"""


def fetch_value_consumer_data(runner: QueryRunner, value_id: str) -> dict:
    """Batch-fetch all data needed to build the consumer chain for a Value.

    Runs Q1 and Q2 and returns a dict. Additional queries (Q3-Q8) are
    invoked as needed by the orchestration layer during recursion.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        value_id: The node_id of the target Value node.

    Returns:
        Dict with keys:
            "receiver_chain": list of Q1 records (dicts)
            "direct_arguments": list of Q2 records (dicts)
            "value_name": name of the Value node
            "value_kind": value_kind of the Value node
    """
    q1_records = runner.execute(Q1_RECEIVER_CHAIN, value_id=value_id)
    q2_records = runner.execute(Q2_DIRECT_ARGUMENTS, value_id=value_id)

    # Fetch value identity for on/on_kind resolution
    identity = runner.execute_single(_Q_VALUE_IDENTITY, value_id=value_id)
    value_name = identity["name"] if identity else None
    value_kind = identity["value_kind"] if identity else None

    return {
        "receiver_chain": [dict(r) for r in q1_records],
        "direct_arguments": [dict(r) for r in q2_records],
        "value_name": value_name,
        "value_kind": value_kind,
    }


def fetch_value_source_data(runner: QueryRunner, value_id: str) -> dict:
    """Fetch source chain data for a Value node.

    Runs Q9 for the initial value and returns source chain info.

    Args:
        runner: Active QueryRunner.
        value_id: The node_id of the Value node to trace from.

    Returns:
        Dict with the Q9 record (or empty dict if no data).
    """
    records = runner.execute(Q9_SOURCE_CHAIN, value_id=value_id)
    if records:
        return dict(records[0])
    return {}
