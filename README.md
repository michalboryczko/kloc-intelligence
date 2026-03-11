# kloc-intelligence

Graph-native code intelligence platform backed by Neo4j. Provides structural code analysis for PHP codebases with bidirectional dependency traversal, inheritance trees, override chains, and rich context queries -- all powered by a persistent graph database.

## Overview

kloc-intelligence imports a `sot.json` (Source of Truth) file produced by the kloc pipeline into Neo4j, then exposes 8 query commands via CLI, JSON output, and an MCP server for AI agent integration.

The graph model stores PHP code symbols (classes, interfaces, methods, properties, values, etc.) as nodes with typed relationships (USES, CONTAINS, EXTENDS, IMPLEMENTS, OVERRIDES, and more). Queries are expressed as Cypher traversals, enabling sub-millisecond lookups on indexed fields and efficient multi-hop BFS expansions.

### Key Features

- **8 query commands**: resolve, usages, deps, context, owners, inherit, overrides, import
- **Neo4j-backed**: persistent graph with indexes, constraints, and batch import
- **MCP server**: JSON-RPC 2.0 stdio protocol for AI agent integration (Claude, etc.)
- **Multi-project support**: single server can query multiple Neo4j databases
- **Contract-compliant output**: JSON output matches kloc-contracts schemas exactly
- **Rich console output**: colored tables and trees via Rich library

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- Neo4j 5.x (Community or Enterprise)
- Docker (recommended for Neo4j)

### Quick Install

```bash
# Clone and enter the project
cd kloc-intelligence

# Install dependencies with uv
uv sync --all-extras

# Start Neo4j via Docker
docker compose up -d

# Wait for Neo4j to be ready, then ensure schema
uv run kloc-intelligence schema ensure
```

### Environment Variables

Configuration is via environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt endpoint |
| `NEO4J_USERNAME` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `kloc-intelligence` | Neo4j password |
| `NEO4J_DATABASE` | `neo4j` | Neo4j database name |

See `.env.example` for a complete template.

## Quick Start

```bash
# 1. Start Neo4j
docker compose up -d

# 2. Import a sot.json file
uv run kloc-intelligence import path/to/sot.json

# 3. Query context for a class
uv run kloc-intelligence context "App\Entity\Order"

# 4. Get JSON output
uv run kloc-intelligence context "App\Entity\Order" --json
```

## Command Reference

### import

Import a `sot.json` file into Neo4j.

```bash
uv run kloc-intelligence import <sot-path> [--no-clear] [--no-validate]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--clear/--no-clear` | `--clear` | Clear database before import |
| `--validate/--no-validate` | `--validate` | Validate node/edge counts after import |

### resolve

Resolve a symbol to its definition location(s). Supports exact FQN, partial match, case-insensitive, and suffix matching.

```bash
uv run kloc-intelligence resolve "App\Entity\Order"
uv run kloc-intelligence resolve "OrderService::createOrder" --json
```

| Flag | Description |
|------|-------------|
| `--json, -j` | Output as JSON |

### usages

Find all usages of a symbol (incoming USES edges) with BFS tree expansion.

```bash
uv run kloc-intelligence usages "App\Entity\Order" --depth 2 --limit 50
uv run kloc-intelligence usages "App\Entity\Order" --json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--depth, -d` | 1 | BFS depth for expansion |
| `--limit, -l` | 100 | Maximum total results |
| `--json, -j` | | Output as JSON |

### deps

Find all dependencies of a symbol (outgoing USES edges) with BFS tree expansion.

```bash
uv run kloc-intelligence deps "App\Service\OrderService" --depth 2
uv run kloc-intelligence deps "App\Service\OrderService" --json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--depth, -d` | 1 | BFS depth for expansion |
| `--limit, -l` | 100 | Maximum total results |
| `--json, -j` | | Output as JSON |

### context

Get bidirectional context: what uses a symbol and what it uses. The most powerful query -- produces definition metadata, USED BY tree, and USES tree with execution flow, argument tracking, and polymorphic analysis.

```bash
uv run kloc-intelligence context "App\Entity\Order"
uv run kloc-intelligence context "App\Service\OrderService::createOrder()" --depth 2 --impl
uv run kloc-intelligence context "App\Entity\Order::$total" --json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--depth, -d` | 1 | BFS depth for expansion |
| `--limit, -l` | 100 | Maximum results per direction |
| `--impl, -i` | off | Include implementations/overrides |
| `--direct` | off | Direct references only |
| `--with-imports` | off | Include PHP import statements |
| `--json, -j` | | Output as JSON |

### owners

Show the structural containment chain for a symbol (e.g., Method -> Class -> File).

```bash
uv run kloc-intelligence owners "App\Service\OrderService::createOrder()"
uv run kloc-intelligence owners "App\Entity\Order::$total" --json
```

| Flag | Description |
|------|-------------|
| `--json, -j` | Output as JSON |

### inherit

Show the inheritance tree for a class, interface, trait, or enum.

```bash
uv run kloc-intelligence inherit "App\Entity\Order" --direction up
uv run kloc-intelligence inherit "App\Component\OrderProcessorInterface" --direction down --depth 3
```

| Flag | Default | Description |
|------|---------|-------------|
| `--direction, -D` | `up` | `up` (ancestors) or `down` (descendants) |
| `--depth, -d` | 5 | Maximum BFS depth |
| `--limit, -l` | 100 | Maximum total results |
| `--json, -j` | | Output as JSON |

### overrides

Show the override chain for a method.

```bash
uv run kloc-intelligence overrides "App\Service\LoggingOrderProcessor::process()" --direction up
uv run kloc-intelligence overrides "App\Component\OrderProcessorInterface::process()" --direction down
```

| Flag | Default | Description |
|------|---------|-------------|
| `--direction, -D` | `up` | `up` (parent methods) or `down` (overriding methods) |
| `--depth, -d` | 5 | Maximum BFS depth |
| `--limit, -l` | 100 | Maximum total results |
| `--json, -j` | | Output as JSON |

### schema (subcommands)

Manage the Neo4j schema (constraints and indexes).

```bash
uv run kloc-intelligence schema ensure    # Create constraints + indexes
uv run kloc-intelligence schema reset     # Drop all data and recreate schema
uv run kloc-intelligence schema verify    # Show schema status and counts
```

## MCP Server

kloc-intelligence includes an MCP (Model Context Protocol) server for AI agent integration. The server communicates via stdio using JSON-RPC 2.0.

### Starting the Server

```bash
# Single-project mode
uv run kloc-intelligence mcp-server --database neo4j

# Multi-project mode with config file
uv run kloc-intelligence mcp-server --config projects.json
```

### MCP Config File Format

```json
{
  "projects": {
    "my-app": "my_app_db",
    "payments": "payments_db"
  }
}
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `kloc_resolve` | Resolve a symbol to its definition location |
| `kloc_usages` | Find all usages of a symbol |
| `kloc_deps` | Find all dependencies of a symbol |
| `kloc_context` | Get bidirectional context (used by + uses) |
| `kloc_owners` | Show structural containment chain |
| `kloc_inherit` | Show inheritance tree |
| `kloc_overrides` | Show override chain for a method |
| `kloc_import` | Import a sot.json file into Neo4j |

### Claude Desktop Integration

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kloc": {
      "command": "uv",
      "args": ["--directory", "/path/to/kloc-intelligence", "run", "kloc-intelligence", "mcp-server"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "kloc-intelligence"
      }
    }
  }
}
```

## Architecture

### Neo4j Graph Model

**Node labels**: Every symbol is a `:Node` with an additional kind-specific label (`:Class`, `:Method`, `:Interface`, `:Property`, `:Value`, `:Call`, `:File`, etc.).

**Node properties**: `node_id` (unique), `kind`, `name`, `fqn`, `symbol`, `file`, `start_line`, `end_line`, `signature`, `documentation`, and more.

**Relationship types** (13 total):

| Relationship | Direction | Meaning |
|-------------|-----------|---------|
| `CONTAINS` | parent -> child | Structural containment (Class -> Method) |
| `USES` | source -> target | Symbol reference |
| `EXTENDS` | child -> parent | Class/interface inheritance |
| `IMPLEMENTS` | class -> interface | Interface implementation |
| `OVERRIDES` | child -> parent | Method override |
| `TYPE_HINT` | symbol -> type | Type annotation |
| `CALLS` | caller -> callee | Method/function call |
| `RECEIVER` | call -> object | Call receiver |
| `ARGUMENT` | call -> value | Argument passing |
| `PRODUCES` | call -> value | Return value |
| `ASSIGNED_FROM` | target -> source | Value assignment |
| `TYPE_OF` | value -> type | Runtime type |
| `RETURN_TYPE` | method -> type | Return type declaration |

**Indexes**: FQN, name, kind, symbol, file on `:Node`; plus kind-specific indexes on `:Class`, `:Method`, `:Interface` FQN fields.

### Source Layout

```
src/
  cli.py              # Typer CLI with all 8 commands
  config.py           # Neo4jConfig from env vars
  db/
    connection.py      # Neo4j driver wrapper
    query_runner.py    # Cypher query executor with logging
    schema.py          # Constraints, indexes, schema management
    importer.py        # sot.json parser + batch Neo4j import
    result_mapper.py   # Neo4j Record -> NodeData conversion
    queries/           # Cypher query modules per command
      resolve.py       # Symbol resolution (6-stage cascade)
      usages.py        # Incoming USES edge queries
      deps.py          # Outgoing USES edge queries
      owners.py        # CONTAINS chain traversal
      inherit.py       # EXTENDS/IMPLEMENTS BFS
      overrides.py     # OVERRIDES BFS
      definition.py    # Structural definition metadata
      context_*.py     # Kind-specific context queries
      helpers.py       # Shared query utilities
  logic/
    definition.py      # Definition builder from query data
    handlers.py        # Reference type handlers
    reference_types.py # Reference type classification
    graph_helpers.py   # Graph traversal utilities
    polymorphic.py     # Interface -> concrete resolution
  models/
    node.py            # NodeData dataclass
    results.py         # Result models (UsagesTreeResult, etc.)
    output.py          # Contract-compliant output serialization
  orchestration/
    usages.py          # Usages command orchestrator
    deps.py            # Deps command orchestrator
    simple.py          # Owners, inherit, overrides orchestrators
    context.py         # Context command orchestrator (dispatch)
    class_context.py   # Class USED BY / USES builders
    interface_context.py
    method_context.py
    property_context.py
    value_context.py
    generic_context.py
  output/
    json_formatter.py  # JSON output
    console.py         # Rich console formatters
  server/
    mcp.py             # MCP server (JSON-RPC 2.0 over stdio)
```

### Query Flow

1. **CLI** parses arguments via Typer
2. **Orchestrator** resolves the symbol, dispatches to kind-specific builders
3. **Query modules** execute Cypher against Neo4j via `QueryRunner`
4. **Result mapper** converts Neo4j Records to `NodeData` objects
5. **Result models** aggregate into tree structures
6. **Output layer** serializes to JSON (contract-compliant) or Rich console

## Testing

```bash
# Run all tests (974 tests)
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_class_context.py -v

# Run with keyword filter
uv run pytest tests/ -k "test_resolve" -v

# Lint check
uv run ruff check src/ tests/
```

Tests use a mock `QueryRunner` to simulate Neo4j responses, so they do not require a running Neo4j instance. Integration tests that need Neo4j are skipped automatically when it is unavailable.

## Performance

### Benchmarks

Run the standalone benchmark suite (requires Neo4j with loaded data):

```bash
uv run python benchmarks/benchmark_queries.py
uv run python benchmarks/benchmark_queries.py --iterations 20 --verbose
uv run python benchmarks/benchmark_queries.py --group context
```

### Typical Results (1154 nodes, 2697 edges)

| Query | Mean | Notes |
|-------|------|-------|
| resolve (exact FQN) | ~1ms | Indexed lookup |
| usages d=1 | ~2ms | Single-hop BFS |
| deps d=1 | ~2ms | Single-hop BFS |
| context class d=1 | ~10ms | Bidirectional + definition |
| context method d=1 | ~15ms | Execution flow + arguments |
| owners | ~1ms | CONTAINS chain |
| inherit | ~2ms | EXTENDS/IMPLEMENTS BFS |
| overrides | ~1ms | OVERRIDES BFS |

Context queries are the most complex, combining definition metadata, USED BY traversal, and USES traversal with execution flow analysis, argument tracking, and polymorphic resolution.

### Large Dataset Performance

For production-scale codebases (721K nodes, 1.6M edges), use the tuned Neo4j config in `docker/neo4j.conf`. Key settings:

- Heap: 4-8 GB
- Page cache: 2-4 GB (should fit entire graph)
- Connection pool: 50 connections

## Comparison with kloc-cli

| Feature | kloc-cli | kloc-intelligence |
|---------|----------|-------------------|
| Backend | In-memory trie + adjacency lists | Neo4j graph database |
| Startup | Loads sot.json every invocation | Import once, query many times |
| Small codebases (<10K nodes) | Faster (no server overhead) | Slightly slower (Neo4j RTT) |
| Large codebases (>100K nodes) | 12s+ load time per query | Sub-second after import |
| Persistent | No | Yes (data survives restarts) |
| MCP Server | Yes (stdio) | Yes (stdio, multi-project) |
| Multi-project | No | Yes (separate Neo4j databases) |
| Query language | Python graph traversal | Cypher (declarative) |
| Output format | Identical JSON contract | Identical JSON contract |

**When to use kloc-cli**: Quick one-off queries on small codebases, no Docker/Neo4j available.

**When to use kloc-intelligence**: Large codebases, persistent analysis, multi-project setups, CI/CD integration, AI agent workflows with many queries.

## Docker

### Development (Neo4j only)

```bash
docker compose up -d
```

### Production (Neo4j + kloc-intelligence)

```bash
cd docker
docker compose up -d
```

See `docker/` directory for production-ready Docker Compose, Dockerfile, and tuned Neo4j configuration.

## License

Internal tool -- not for public distribution.
