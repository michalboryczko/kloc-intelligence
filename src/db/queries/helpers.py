"""Cypher helper queries for pre-fetching data needed by handlers and reference type inference.

These queries are used by the orchestration layer to pre-resolve containment,
call matching, receiver identity, and type_hint classification data before
passing it to the pure logic functions in src/logic/.
"""

# =============================================================================
# Containment Traversal
# =============================================================================

# Resolve the nearest containing Method/Function by walking CONTAINS upward.
# Returns the first ancestor that is a Method or Function.
CONTAINING_METHOD = """
MATCH (n:Node {node_id: $id})
OPTIONAL MATCH path = (n)<-[:CONTAINS*1..10]-(ancestor)
WHERE ancestor.kind IN ['Method', 'Function']
WITH ancestor, path ORDER BY length(path) ASC LIMIT 1
RETURN ancestor.node_id AS id, ancestor.fqn AS fqn, ancestor.kind AS kind
"""

# Resolve the nearest containing Class/Interface/Trait/Enum.
CONTAINING_CLASS = """
MATCH (n:Node {node_id: $id})
OPTIONAL MATCH path = (n)<-[:CONTAINS*1..10]-(ancestor)
WHERE ancestor.kind IN ['Class', 'Interface', 'Trait', 'Enum']
WITH ancestor, path ORDER BY length(path) ASC LIMIT 1
RETURN ancestor.node_id AS id, ancestor.fqn AS fqn, ancestor.kind AS kind,
       ancestor.file AS file, ancestor.start_line AS start_line
"""

# Check if source is internal to target class (source is contained within
# the target class's containment subtree).
IS_INTERNAL = """
MATCH (source:Node {node_id: $source_id})
OPTIONAL MATCH path = (source)<-[:CONTAINS*1..10]-(cls:Node {node_id: $class_id})
RETURN count(path) > 0 AS is_internal
"""

# =============================================================================
# Call Matching
# =============================================================================

# Find a Call node matching a usage location.
# Searches the source's Call children for one at the specified file/line.
# For constructors, allows +/- 1 line tolerance.
FIND_CALL = """
MATCH (source:Node {node_id: $source_id})-[:CONTAINS]->(call:Node)
WHERE call.kind = 'Call' AND call.file = $file
WITH call,
     call.start_line AS call_line
WHERE call_line = $line OR (call.call_kind = 'constructor' AND abs(call_line - $line) <= 1)
OPTIONAL MATCH (call)-[:CALLS]->(callee)
RETURN call.node_id AS id, call.call_kind AS kind,
       call.access_chain AS access_chain,
       callee.node_id AS callee_id
LIMIT 1
"""

# Find Call node and resolve its receiver identity.
# Returns the access chain, receiver kind, and call kind.
FIND_CALL_WITH_RECEIVER = """
MATCH (source:Node {node_id: $source_id})-[:CONTAINS]->(call:Node)
WHERE call.kind = 'Call' AND call.file = $file
WITH call,
     call.start_line AS call_line
WHERE call_line = $line OR (call.call_kind = 'constructor' AND abs(call_line - $line) <= 1)
OPTIONAL MATCH (call)-[:RECEIVER]->(recv:Node)
OPTIONAL MATCH (call)-[:CALLS]->(callee)
RETURN call.node_id AS id,
       call.call_kind AS call_kind,
       call.access_chain AS access_chain,
       recv.value_kind AS recv_value_kind,
       callee.node_id AS callee_id
LIMIT 1
"""

# =============================================================================
# Reference Type Classification Data
# =============================================================================

# Pre-fetch all data needed for infer_reference_type in a single query.
# For each incoming edge to a target, determines:
# - Whether any Argument child of the source has a type_hint to the target
# - Whether the source Method/Function itself has a type_hint to the target
# - Whether the parent class has a Property with type_hint (constructor promotion)
# - Whether the source class has a Property with type_hint
REF_TYPE_DATA = """
MATCH (target:Node {node_id: $target_id})<-[e]-(source:Node)
WHERE type(e) IN ['USES', 'EXTENDS', 'IMPLEMENTS', 'USES_TRAIT']
WITH source, e, target
OPTIONAL MATCH (source)-[:CONTAINS]->(arg:Node {kind: 'Argument'})-[:TYPE_HINT]->(target)
WITH source, e, target, count(arg) > 0 AS has_arg_type_hint
OPTIONAL MATCH (source)-[:TYPE_HINT]->(target)
WHERE source.kind IN ['Method', 'Function']
WITH source, e, target, has_arg_type_hint, count(*) > 0 AS has_return_type_hint
OPTIONAL MATCH (source)<-[:CONTAINS]-(parent_cls:Node)-[:CONTAINS]->(prop:Node {kind: 'Property'})-[:TYPE_HINT]->(target)
WHERE source.kind IN ['Method', 'Function'] AND source.name = '__construct'
WITH source, e, target, has_arg_type_hint, has_return_type_hint,
     count(prop) > 0 AS has_class_property_type_hint
OPTIONAL MATCH (source)-[:CONTAINS]->(src_prop:Node {kind: 'Property'})-[:TYPE_HINT]->(target)
WHERE source.kind IN ['Class', 'Interface', 'Trait', 'Enum']
WITH source, e, target, has_arg_type_hint, has_return_type_hint,
     has_class_property_type_hint, count(src_prop) > 0 AS has_source_class_property_type_hint
RETURN source.node_id AS source_id,
       source.kind AS source_kind,
       source.name AS source_name,
       source.fqn AS source_fqn,
       source.file AS source_file,
       source.start_line AS source_start_line,
       source.signature AS source_signature,
       type(e) AS edge_type,
       e.loc_file AS loc_file,
       e.loc_line AS loc_line,
       target.kind AS target_kind,
       has_arg_type_hint,
       has_return_type_hint,
       has_class_property_type_hint,
       has_source_class_property_type_hint
"""

# =============================================================================
# Property Resolution (for PropertyTypeHandler constructor promotion)
# =============================================================================

# Find the Property node in the parent class that has a type_hint to the target.
# Used for constructor promotion when source is __construct.
FIND_PROPERTY_WITH_TYPE_HINT = """
MATCH (method:Node {node_id: $method_id})<-[:CONTAINS]-(cls:Node)
      -[:CONTAINS]->(prop:Node {kind: 'Property'})-[:TYPE_HINT]->(target:Node {node_id: $target_id})
RETURN prop.node_id AS id, prop.fqn AS fqn, prop.file AS file, prop.start_line AS start_line
LIMIT 1
"""

# =============================================================================
# Promoted Parameter Resolution
# =============================================================================

# Resolve a promoted constructor parameter FQN to its Property FQN.
# Given a param FQN (e.g., "Order::__construct().$id"), find the Property
# that has an ASSIGNED_FROM edge to the matching Value(parameter) node.
# This is the Neo4j equivalent of kloc-cli's resolve_promoted_property_fqn().
RESOLVE_PROMOTED_PARAM = """
MATCH (prop:Node {kind: 'Property'})-[:ASSIGNED_FROM]->(param:Value {value_kind: 'parameter'})
WHERE param.fqn = $param_fqn
RETURN prop.fqn AS property_fqn
LIMIT 1
"""

# =============================================================================
# Argument Info
# =============================================================================

# Get argument-to-parameter mappings for a Call node.
CALL_ARGUMENTS = """
MATCH (call:Node {node_id: $call_id})-[a:ARGUMENT]->(value:Node)
RETURN value.node_id AS value_id,
       a.position AS position,
       a.expression AS expression,
       a.parameter AS parameter,
       value.name AS value_name,
       value.value_kind AS value_kind
ORDER BY a.position
"""
