# Migration Guide: kloc-cli to kloc-intelligence

This guide covers migrating from kloc-cli (in-memory trie-based) to kloc-intelligence (Neo4j-backed).

## Feature Comparison

| Feature | kloc-cli | kloc-intelligence |
|---------|----------|-------------------|
| resolve | `--sot <path>` per invocation | import once, query many times |
| usages | identical | identical |
| deps | identical | identical |
| context | identical JSON output | identical JSON output |
| owners | identical | identical |
| inherit | identical | identical |
| overrides | identical | identical |
| MCP server | single sot.json | multi-project (multiple databases) |
| Output format | JSON (contract-compliant) | JSON (contract-compliant) |

Both tools produce identical JSON output for the same queries on the same data. The contract tests validate this parity.

## Installation Changes

### kloc-cli

```bash
cd kloc-cli
uv sync
```

### kloc-intelligence

```bash
cd kloc-intelligence
uv sync

# Start Neo4j
docker compose up -d

# Ensure schema
uv run kloc-intelligence schema ensure

# Import data (one time)
uv run kloc-intelligence import path/to/sot.json
```

## CLI Flag Changes

### Data Source

kloc-cli requires `--sot` on every invocation:

```bash
# kloc-cli
kloc-cli context "App\Entity\Order" --sot data/sot.json
kloc-cli usages "App\Entity\Order" --sot data/sot.json
```

kloc-intelligence uses environment variables for Neo4j connection. Data is imported once:

```bash
# kloc-intelligence: import once
kloc-intelligence import data/sot.json

# Then query without specifying data source
kloc-intelligence context "App\Entity\Order"
kloc-intelligence usages "App\Entity\Order"
```

### Flag Mapping

| kloc-cli | kloc-intelligence | Notes |
|----------|-------------------|-------|
| `--sot <path>` | `NEO4J_URI` env var | Data is pre-imported |
| `--json` | `--json` | Same |
| `--depth N` | `--depth N` | Same |
| `--limit N` | `--limit N` | Same |
| `--impl` | `--impl` | Same |
| `--direct` | `--direct` | Same |
| `--with-imports` | `--with-imports` | Same |
| `--direction up/down` | `--direction up/down` | Same |

### New Commands (kloc-intelligence only)

```bash
# Schema management
kloc-intelligence schema ensure
kloc-intelligence schema reset
kloc-intelligence schema verify

# Data import
kloc-intelligence import <sot.json> [--no-clear] [--no-validate]
```

## Performance Differences

### Startup Cost

| Scenario | kloc-cli | kloc-intelligence |
|----------|----------|-------------------|
| First query | Load sot.json (1-12s depending on size) | Import sot.json (one-time, 5-30s) |
| Subsequent queries | Load again (1-12s each time) | ~0ms (data persists in Neo4j) |

### Query Latency

| Dataset Size | kloc-cli | kloc-intelligence |
|-------------|----------|-------------------|
| 1K nodes | ~1ms + 1s load | ~1-15ms (no load) |
| 15K nodes | ~5ms + 3s load | ~1-20ms (no load) |
| 721K nodes | ~50ms + 12s load | ~5-50ms (no load) |

For interactive use or repeated queries, kloc-intelligence is significantly faster because the 12s+ sot.json loading cost is paid once during import, not on every query.

### Memory

- **kloc-cli**: Holds entire graph in Python memory (can use 1-4 GB for large codebases)
- **kloc-intelligence**: Graph lives in Neo4j; Python process uses ~50-100 MB

## MCP Server Changes

### kloc-cli MCP

```json
{
  "mcpServers": {
    "kloc": {
      "command": "uv",
      "args": ["--directory", "/path/to/kloc-cli", "run", "kloc-cli", "mcp-server", "--sot", "/path/to/sot.json"]
    }
  }
}
```

### kloc-intelligence MCP

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

### Multi-Project (kloc-intelligence only)

```json
{
  "mcpServers": {
    "kloc": {
      "command": "uv",
      "args": ["--directory", "/path/to/kloc-intelligence", "run", "kloc-intelligence", "mcp-server", "--config", "/path/to/projects.json"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "kloc-intelligence"
      }
    }
  }
}
```

With `projects.json`:

```json
{
  "projects": {
    "my-app": "my_app_db",
    "payments": "payments_db"
  }
}
```

Each project gets its own Neo4j database. Import data per database:

```bash
NEO4J_DATABASE=my_app_db kloc-intelligence import my-app/sot.json
NEO4J_DATABASE=payments_db kloc-intelligence import payments/sot.json
```

MCP tool calls include a `project` parameter to select which database to query.

## When to Use Which

### Use kloc-cli when:

- Quick one-off queries on small codebases (<15K nodes)
- No Docker or Neo4j available
- CI/CD where you only need a single query per job
- Testing or development with frequently changing sot.json

### Use kloc-intelligence when:

- Large codebases (>100K nodes) where load time dominates
- Multiple queries per session (interactive analysis)
- Multi-project environments
- AI agent workflows with many tool calls
- Persistent analysis that survives process restarts
- Environments where Neo4j is already available

## Migration Checklist

1. Install Neo4j (Docker recommended): `docker compose up -d`
2. Install kloc-intelligence: `uv sync`
3. Create schema: `uv run kloc-intelligence schema ensure`
4. Import your sot.json: `uv run kloc-intelligence import <path>`
5. Verify data: `uv run kloc-intelligence schema verify`
6. Test a query: `uv run kloc-intelligence context "YourClass" --json`
7. Compare output with kloc-cli to confirm parity
8. Update MCP server config in claude_desktop_config.json
9. Remove `--sot` flags from any scripts/automation
