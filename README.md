# kloc-intelligence

Graph-native code intelligence platform for PHP codebases. Loads a `sot.json`
(Source of Truth) and optional Symfony flow metadata into Neo4j, optionally
enriches the graph with LLM explanations and Qdrant vector embeddings, and
exposes everything through a CLI plus an MCP server for AI agents.

```
PHP source ─► kloc-indexer-php ─► index.json
                                                   │
              kloc-mapper      ─► sot.json         │
              kloc-symfony     ─► symfony-kloc.json│
                                                   ▼
                                       kloc-intelligence
                                          ├── Neo4j (graph)
                                          └── Qdrant (vectors)
                                                   │
                                                   ▼
                              CLI / MCP server / Cypher / Qdrant API
```

Compared to `kloc-cli` (stateless, reads `sot.json` each invocation),
kloc-intelligence is **stateful**: it persists the graph in Neo4j, supports
multi-hop Cypher traversals, and adds AI features (per-node explanations,
flow-level business summaries, semantic search) backed by Qdrant.

## Features

- **22 commands** spanning schema management, structural traversal, source
  reading, AI enrichment, semantic search, and Symfony flow surfaces
  (messages / events / HTTP clients).
- **Neo4j graph model** — 13 node kinds, 13 edge types, plus first-class
  `:Flow`, `:Message`, `:Event`, `:HttpClient` nodes wired by
  `FLOW_ENTRY`, `FLOW_ENTRY_CLASS`, `EMITS`, `USES_HTTP_CLIENT`,
  `HANDLED_BY`, and `OF_TYPE` edges.
- **Per-operation provider config** — point LLM and embeddings at any
  OpenAI-compatible endpoint independently (OpenRouter, Google Gemini,
  OpenAI, mixed).
- **MCP server** — JSON-RPC 2.0 stdio protocol, 22 tools, multi-project
  support.
- **Contract-compliant output** — JSON matches the kloc-contracts schemas
  used by every other tool in the pipeline.

## Quick start

```bash
cd kloc-intelligence
uv sync --all-extras
docker compose up -d                          # Neo4j 5 + Qdrant 1.12
cp .env.example .env                          # set LLM_API_KEY + EMBEDDING_API_KEY
uv run kloc-intelligence schema ensure

# Ingest the structural graph
uv run kloc-intelligence import /path/to/sot.json

# (Optional) Symfony flows
uv run kloc-intelligence import-flows /path/to/.kloc/symfony-kloc.json

# (Optional) AI enrichment
uv run kloc-intelligence enrich
uv run kloc-intelligence enrich-flows

# Query
uv run kloc-intelligence context "App\\Service\\OrderService::createOrder"
uv run kloc-intelligence flows OrderController::create
uv run kloc-intelligence search "create a new customer order"
```

Detailed guides live under `docs/usage/kloc-intelligence/`:

- [configuration.md](../docs/usage/kloc-intelligence/configuration.md) —
  install, env vars, provider recipes
- [data-setup.md](../docs/usage/kloc-intelligence/data-setup.md) —
  pipeline order (import → flows → enrich → enrich-flows)
- [cli.md](../docs/usage/kloc-intelligence/cli.md) — every CLI command
- [mcp.md](../docs/usage/kloc-intelligence/mcp.md) — MCP server + 22 tools

## Commands at a glance

| Group | Commands |
| --- | --- |
| Schema | `schema ensure` · `schema verify` · `schema reset` |
| Ingest | `import` · `import-flows` |
| Structural | `resolve` · `owners` · `usages` · `deps` · `context` · `inherit` · `overrides` · `flows` |
| Symfony entities | `messages` · `events` · `http-clients` |
| Source | `source` · `chunks` |
| AI | `enrich` · `enrich-status` · `enrich-flows` · `explain` · `search` |
| Server | `mcp-server` |

All commands accept `--json` for machine-readable output. AI commands accept
`--debug` to log LLM prompts and embedding bodies.

## Configuration

The two providers are independent. Both default to OpenRouter.

```ini
# Neo4j (required)
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=kloc-intelligence
NEO4J_DATABASE=neo4j

# Qdrant (required for AI features)
QDRANT_URL=http://localhost:6333

# LLM provider — used by enrich, explain, enrich-flows
LLM_API_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-...
LLM_MODEL=minimax/minimax-m2.7

# Embedding provider — used by enrich, enrich-flows, search
EMBEDDING_API_URL=https://openrouter.ai/api/v1
EMBEDDING_API_KEY=sk-or-v1-...
EMBEDDING_MODEL=qwen/qwen3-embedding-8b
EMBEDDING_DIMENSION=4096

# Project metadata
KLOC_PROJECT_ROOT=/path/to/php-project          # required for source / chunks / enrich
KLOC_PROJECT_NAME=default
```

Native Google Gemini works out of the box via the OpenAI-compatible endpoint
(`https://generativelanguage.googleapis.com/v1beta/openai/`); a compat shim in
`src/ai/_haystack_compat.py` handles Gemini's missing `usage` field. See
[configuration.md](../docs/usage/kloc-intelligence/configuration.md) for
provider recipes.

## Architecture

```
src/
├── cli.py              # Typer entry point — every CLI command
├── config.py           # Neo4jConfig from env
├── server/
│   └── mcp.py          # MCP server (JSON-RPC 2.0 over stdio), 22 tools
├── db/
│   ├── connection.py   # Neo4j driver wrapper
│   ├── query_runner.py # Cypher executor with logging
│   ├── schema.py       # Constraints + 16 indexes (incl. Message/Event/HttpClient)
│   ├── importer.py     # sot.json → Neo4j (msgspec parsing)
│   ├── flow_importer.py# symfony-kloc.json v3 → :Flow + :Message + :Event + :HttpClient via MERGE-reconcile
│   ├── result_mapper.py# Record → NodeData
│   └── queries/        # One module per structural query (resolve, deps, …, flows)
├── orchestration/
│   ├── context.py      # Kind-based dispatcher (Class/Method/Property/Value/…)
│   ├── class_context.py / interface_context.py / method_context.py / …
│   ├── usages.py / deps.py / simple.py
│   └── …               # Reference-type-aware tree builders
├── logic/              # Pure logic: handlers, reference types, graph helpers
├── models/
│   ├── node.py         # NodeData dataclass
│   ├── results.py      # ContextResult, UsagesTreeResult, etc.
│   └── output.py       # Contract-compliant ContextOutput (1-based lines, camelCase)
├── ai/
│   ├── config.py       # AIConfig + LLMProviderConfig + EmbeddingProviderConfig
│   ├── pipelines.py    # Haystack pipelines (explain / embed / search / flow)
│   ├── _haystack_compat.py  # Tolerant embedders for Gemini's missing `usage`
│   ├── enricher.py     # Class/Method enrichment orchestrator
│   ├── flow_enricher.py# :Flow business-process summary orchestrator (v3 dispatch context)
│   ├── flow_qdrant.py  # Per-flow Qdrant filter-delete + point-scroll utilities
│   ├── chunker.py      # Token-bounded chunking for large classes
│   └── source_reader.py# File + line-range source reader
└── output/             # Rich console + JSON output formatters (incl. flows.py)
```

### Graph schema

`:Node` carries every PHP symbol (`Class`, `Method`, `Interface`, `Property`,
`Value`, `Call`, …). `:Flow` carries Symfony entry points (HTTP routes,
message handlers, event subscribers, CLI commands). `:Message`, `:Event`,
and `:HttpClient` are first-class siblings of `:Flow` for dispatched
messages, dispatched events, and outbound HTTP integrations respectively.

| Relationship | Direction | Meaning |
| --- | --- | --- |
| `CONTAINS` | parent → child | Structural containment |
| `USES` | source → target | Symbol reference |
| `EXTENDS` | child → parent | Class/interface inheritance |
| `IMPLEMENTS` | class → interface | Interface implementation |
| `OVERRIDES` | child → parent | Method override |
| `TYPE_HINT` | symbol → type | Type annotation |
| `CALLS` | caller → callee | Method/function call |
| `RECEIVER` | call → object | Call receiver |
| `ARGUMENT` | call → value | Argument passing |
| `PRODUCES` | call → value | Return value |
| `ASSIGNED_FROM` | target → source | Value assignment |
| `TYPE_OF` | value → type | Runtime type |
| `RETURN_TYPE` | method → type | Return type declaration |
| `FLOW_ENTRY` | flow → method | Symfony flow entry point |
| `FLOW_ENTRY_CLASS` | flow → class | Owning class for the entry method |
| `EMITS` | flow / call → message / event | Outbound dispatch site |
| `USES_HTTP_CLIENT` | flow / call → http_client | Outbound HTTP integration site |
| `HANDLED_BY` | message / event → flow | Inbound dispatch handler (events carry `priority`) |
| `OF_TYPE` | message / event / http_client → class | Optional class link (absent for vendor classes) |

Indexes are created on `:Node.fqn` / `name` / `kind` / `symbol` / `file` /
`explanation`, on `:Class.fqn` / `:Method.fqn` / `:Interface.fqn`, on
`:Value.kind` and `:Call.kind`, on `:Flow.flow_id` / `:Flow.type`, and on
`:Message.fqn` / `:Event.fqn` / `:HttpClient.service_id`. Uniqueness
constraints exist on `:Flow.flow_id`, `:Message.id`, `:Event.id`,
`:HttpClient.id`. Re-importing `symfony-kloc.json` is **idempotent**:
`:Flow.explanation` and the `flow_explain_embeddings` Qdrant collection
survive structural-only changes; orphan flow embeddings are pruned by
`flow_id` filter (the collection is never dropped).

### Qdrant collections

| Collection | What it embeds | Populated by |
| --- | --- | --- |
| `code_embeddings` | Source-code chunks of Class/Method nodes | `enrich` |
| `explain_embeddings` | LLM-authored explanations | `enrich` |
| `flow_explain_embeddings` | Flow business-process summaries | `enrich-flows` |

`search` queries all three by default, dedupes by `node_id`, and returns the
top hits.

## Development

```bash
uv sync --extra dev --extra ai

# Lint + format check
uv run ruff check src tests benchmarks
uv run ruff format --check src tests benchmarks

# Static analysis
uv run mypy src

# Tests (unit + Neo4j integration; snapshots + Haystack tests skipped without fixtures)
uv run pytest -q --deselect tests/test_snapshot.py --deselect tests/test_flow_enricher.py

# Apply formatter in-place
uv run ruff format src tests benchmarks
```

CI runs lint + format check + mypy + pytest on every push and PR — see
`.github/workflows/ci.yml`. Neo4j 5 and Qdrant 1.12 are spun up as service
containers in the test job.

### Snapshot tests

`tests/test_snapshot.py` reuses the parent-repo snapshot corpus
(`artifacts/kloc-dev/context-final/sot.json` + `tests/snapshot-2103260323.json`)
to verify context-query output stays contract-compliant. They are skipped in
CI because the parent repo's `artifacts/` directory isn't checked in.

```bash
uv run pytest tests/test_snapshot.py -v
```

### Haystack-dependent tests

`tests/test_flow_enricher.py` requires `--extra ai` and is skipped in CI by
default because Haystack pipelines are expensive to import. Run locally:

```bash
uv run --extra ai pytest tests/test_flow_enricher.py -v
```

## MCP server

```bash
uv run kloc-intelligence mcp-server                                  # default db
uv run kloc-intelligence mcp-server --database my_app_db             # named db
uv run kloc-intelligence mcp-server --config /path/to/projects.json  # multi-project
```

Wire into Claude Code's `~/.claude.json` (or any MCP-aware client):

```json
{
  "mcpServers": {
    "kloc-intelligence": {
      "command": "uv",
      "args": ["run", "kloc-intelligence", "mcp-server"],
      "cwd": "/path/to/kloc-intelligence",
      "env": {
        "KLOC_PROJECT_ROOT": "/path/to/php-project"
      }
    }
  }
}
```

The 22 tools then become callable as
`mcp__kloc-intelligence__kloc_context`, etc. See
[mcp.md](../docs/usage/kloc-intelligence/mcp.md) for the full catalog.

## Performance

| Query | Mean (reference project, 1154 nodes) |
| --- | --- |
| `resolve` (exact FQN) | ~1 ms |
| `usages` / `deps` (d=1) | ~2 ms |
| `context` class / method (d=1) | 10-15 ms |
| `inherit` / `overrides` | ~2 ms |
| `enrich` (per node) | 1 LLM + 2 embedding calls (~2-5 s) |
| `enrich-flows` (per flow) | 1 LLM + 1 embedding call (~2-5 s) |
| `search` (3 collections merged) | ~50 ms |

For datasets >500K nodes bump `NEO4J_HEAP_MAX` (see `docker-compose.yml`).
The largest tested graph is 721K nodes / 1.6M edges from a real codebase.

## License

Internal tool — not for public distribution.
