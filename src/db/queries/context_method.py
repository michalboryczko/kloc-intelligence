"""Cypher queries for Method node context (execution flow).

Pre-fetches all data needed to build the execution flow (USES) section for a
Method node. Batch functions minimise round-trips; orchestration assembles.
"""

from ..query_runner import QueryRunner

# =============================================================================
# Q1: All Calls Within a Method
# =============================================================================

Q1_METHOD_CALLS = """
MATCH (method:Node {node_id: $method_id})-[:CONTAINS]->(call:Call)
OPTIONAL MATCH (call)-[:CALLS]->(callee)
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(recv_src:Call)
OPTIONAL MATCH (call)-[:PRODUCES]->(result:Value)
OPTIONAL MATCH (local:Value {value_kind: 'local'})-[:ASSIGNED_FROM]->(result)
OPTIONAL MATCH (local)-[:TYPE_OF]->(local_type)
RETURN call.node_id AS call_id,
       call.call_kind AS call_kind,
       call.name AS call_name,
       call.file AS call_file,
       call.start_line AS call_line,
       callee.node_id AS callee_id,
       callee.fqn AS callee_fqn,
       callee.kind AS callee_kind,
       callee.name AS callee_name,
       callee.signature AS callee_signature,
       callee.file AS callee_file,
       callee.start_line AS callee_start_line,
       recv.node_id AS recv_id,
       recv.value_kind AS recv_value_kind,
       recv.name AS recv_name,
       recv.file AS recv_file,
       recv.start_line AS recv_start_line,
       recv_src.node_id AS recv_source_call_id,
       result.node_id AS result_id,
       local.node_id AS local_id,
       local.fqn AS local_fqn,
       local.name AS local_name,
       local.start_line AS local_line,
       local_type.name AS local_type_name
ORDER BY call.node_id
"""

# =============================================================================
# Q2: Argument Edges for All Calls
# =============================================================================

Q2_CALL_ARGUMENTS = """
MATCH (method:Node {node_id: $method_id})-[:CONTAINS]->(call:Call)
MATCH (call)-[a:ARGUMENT]->(val:Value)
OPTIONAL MATCH (val)-[:TYPE_OF]->(vtype)
RETURN call.node_id AS call_id,
       a.position AS position,
       a.expression AS expression,
       a.parameter AS parameter,
       val.node_id AS value_id,
       val.value_kind AS value_kind,
       val.name AS value_name,
       val.fqn AS value_fqn,
       vtype.name AS value_type
ORDER BY call.node_id, a.position
"""

# =============================================================================
# Q3: Consumed Call Detection
# =============================================================================

Q3_CONSUMED_CALLS = """
MATCH (method:Node {node_id: $method_id})-[:CONTAINS]->(consumer:Call)
MATCH (consumer)-[:RECEIVER]->(recv:Value {value_kind: 'result'})<-[:PRODUCES]-(src:Call)
WHERE (method)-[:CONTAINS]->(src)
RETURN DISTINCT src.node_id AS consumed_call_id

UNION

MATCH (method:Node {node_id: $method_id})-[:CONTAINS]->(consumer:Call)
MATCH (consumer)-[a:ARGUMENT]->(arg:Value {value_kind: 'result'})<-[:PRODUCES]-(src:Call)
WHERE (method)-[:CONTAINS]->(src)
RETURN DISTINCT src.node_id AS consumed_call_id
"""

# =============================================================================
# Q4: Type References (structural uses edges not covered by Calls)
# =============================================================================

Q4_TYPE_REFERENCES = """
MATCH (method:Node {node_id: $method_id})-[e:USES]->(target:Node)
WHERE target.kind IN ['Class', 'Interface', 'Trait', 'Enum']
OPTIONAL MATCH (method)-[:CONTAINS]->(arg:Argument)-[:TYPE_HINT]->(target)
WITH method, e, target, count(arg) > 0 AS has_arg_th
OPTIONAL MATCH (method)-[rth:TYPE_HINT]->(target)
WITH method, e, target, has_arg_th, count(rth) > 0 AS has_ret_th
RETURN target.node_id AS target_id,
       target.fqn AS target_fqn,
       target.kind AS target_kind,
       target.signature AS target_signature,
       target.file AS target_file,
       target.start_line AS target_start_line,
       e.loc_file AS file, e.loc_line AS line,
       has_arg_th, has_ret_th
"""

# =============================================================================
# Q5: Receiver Access Chain
# =============================================================================

Q5_RECEIVER_CHAIN = """
MATCH (call:Node {node_id: $call_id})-[:RECEIVER]->(recv:Value)
OPTIONAL MATCH (recv)<-[:PRODUCES]-(src_call:Call)-[:CALLS]->(prop)
OPTIONAL MATCH (src_call)-[:RECEIVER]->(src_recv:Value)
RETURN recv.value_kind AS recv_kind, recv.name AS recv_name,
       src_call.call_kind AS src_call_kind,
       prop.fqn AS prop_fqn, prop.name AS prop_name,
       src_recv.value_kind AS src_recv_kind, src_recv.name AS src_recv_name
"""


def fetch_method_execution_data(runner: QueryRunner, method_id: str) -> dict:
    """Batch-fetch all data needed to build execution flow for a Method.

    Runs Q1, Q2, Q3 in sequence and returns a dict. Q4 and Q5 are invoked
    separately by the orchestration layer.

    Args:
        runner: Active QueryRunner connected to Neo4j.
        method_id: The node_id of the target Method node.

    Returns:
        Dict with keys:
            "calls":         list of Q1 records (dicts) — all calls in the method
            "arguments":     list of Q2 records (dicts) — argument edges
            "consumed_ids":  set of call_ids that are consumed by other calls
    """
    q1_records = runner.execute(Q1_METHOD_CALLS, method_id=method_id)
    q2_records = runner.execute(Q2_CALL_ARGUMENTS, method_id=method_id)
    q3_records = runner.execute(Q3_CONSUMED_CALLS, method_id=method_id)

    calls = [dict(r) for r in q1_records]
    arguments = [dict(r) for r in q2_records]
    consumed_ids = {r["consumed_call_id"] for r in q3_records if r["consumed_call_id"]}

    return {
        "calls": calls,
        "arguments": arguments,
        "consumed_ids": consumed_ids,
    }
