# Changelog

## v0.1.0 (2026-03-06)

Initial release of kloc-intelligence -- a Neo4j-backed reimplementation of kloc-cli with identical query semantics and contract-compliant output.

### T01: Project Scaffold & Infrastructure
- Python 3.11+ project with uv package manager
- pyproject.toml with neo4j, typer, rich, msgspec dependencies
- Docker Compose for Neo4j development instance
- Ruff linting configuration
- pytest test framework setup

### T02: Data Import Pipeline
- sot.json parser using msgspec for fast decoding
- Batch import of nodes with kind-specific Neo4j labels (Class, Method, etc.)
- Batch import of edges with typed relationships (USES, CONTAINS, etc.)
- Signature extraction from method/function documentation
- CONTAINS edge ordinal tracking for deterministic child ordering
- Import validation with node/edge count verification

### T03: Snapshot Test Infrastructure
- Snapshot comparison framework for output regression testing
- Diff reporting for test failures
- Test data fixtures from artifacts/kloc-dev/context-final/sot.json (1154 nodes, 2697 edges)

### T04: Graph Query Foundation
- Neo4jConnection wrapper with pooling and timeout configuration
- QueryRunner with execution timing and logging
- Result mapper: Neo4j Record to NodeData conversion
- Symbol resolution with 6-stage cascade (exact FQN, case-insensitive, suffix, name, no-parens, contains)
- Neo4j schema management (constraints, indexes, drop, verify)

### T05: Usages & Deps Commands
- Usages query: incoming USES edges with container member expansion
- Deps query: outgoing USES edges with container member expansion
- BFS tree expansion with global visited set and count limit
- Location resolution: edge location preferred, fallback to node location
- UsagesTreeResult and DepsTreeResult models with to_dict() serialization

### T06: Owners, Inherit, Overrides Commands
- Owners: CONTAINS chain traversal from target to File root
- Inherit: EXTENDS/IMPLEMENTS BFS with direction (up/down) and depth limit
- Overrides: OVERRIDES BFS with direction (up/down) and depth limit
- All three commands support by-ID and by-query entry points

### T07: Reference Types & Handlers
- Reference type classification (instantiation, static_call, method_call, extends, implements, etc.)
- Handler dispatch for different reference patterns
- Access chain resolution for property/method references
- On-kind classification (property, param, local, self)

### T08: Definition & Context Models
- DefinitionInfo model with full structural metadata
- Definition builder from Neo4j query data
- ContextResult, ContextEntry, MemberRef, ArgumentInfo models
- Argument-to-parameter mapping at call sites
- Source chain tracking for value provenance

### T09: Class Context (USED BY + USES)
- Class USED BY: instantiation sites, type hint references, extends/implements references
- Class USES: constructor dependencies, method execution flows
- Caller chain builder for method-level USED BY expansion
- Multi-site grouping for classes referenced from multiple locations in the same method

### T10: Interface & Method Context + Polymorphic Support
- Interface USED BY: implementors, type hint references
- Interface USES: method signatures, with optional concrete implementation expansion
- Method USES: type references + execution flow (calls, arguments, receivers)
- Polymorphic resolution: interface method -> concrete implementor methods
- get_concrete_implementors() for --impl flag support

### T11: Value & Property Context
- Value consumer chain: traverses USES edges to find where values flow
- Value source chain: traverses ASSIGNED_FROM/PRODUCES to find value origins
- Property USED BY: access sites with caller chain expansion
- Property USES: type information, assignment sources
- Variable-centric flow entries with source_call tracking

### T12: Type Resolution - Context Orchestrator & Wiring
- Central context orchestrator (execute_context) with kind-based dispatch
- Constructor redirect: __construct() queries redirect to containing class
- USED BY dispatch: Value, Property, Class, Interface, Method, generic
- USES dispatch: Method/Function, Value, Property, Class, Interface, generic
- Deferred callback wiring to break circular dependencies

### T13: CLI Interface & MCP Server
- Typer CLI with all 8 commands (resolve, usages, deps, context, owners, inherit, overrides, import)
- Schema subcommands (ensure, reset, verify)
- Rich console output: colored tables, trees, definition sections
- MCP server: JSON-RPC 2.0 over stdio
- Multi-project MCP support via config file (project -> Neo4j database mapping)
- 8 MCP tools matching CLI commands

### T14: Contract Tests & Behavioral Validation
- Contract test suite validating JSON output against kloc-contracts schemas
- Output model (ContextOutput) as single conversion point for contract compliance
- Line number normalization (0-based internal, 1-based output)
- Mode-dependent field suppression (class-level vs method-level context)
- Edge case tests for missing data, empty results, and boundary conditions

### T15: Performance Benchmarks & Documentation
- Standalone benchmark suite (benchmarks/benchmark_queries.py) covering all 8 command types
- README with installation, quick start, command reference, architecture overview
- Docker production config (Dockerfile, docker-compose.yml, neo4j.conf tuned for 721K nodes)
- Migration guide from kloc-cli to kloc-intelligence
- This changelog

### Test Coverage
- 974 tests, all passing
- Mock-based unit tests (no Neo4j required for regular test runs)
- Integration tests with auto-skip when Neo4j is unavailable
- Snapshot tests for output regression detection
