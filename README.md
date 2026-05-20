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
- **MCP server** — JSON-RPC 2.0 over stdio **or** Streamable HTTP, 22 tools, multi-project
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

Full documentation lives at [`docs/v3/kloc-intelligence/`](../docs/v3/kloc-intelligence/index.md), organized via the [Diátaxis](https://diataxis.eu/) framework:

| Section | When to use |
| --- | --- |
| [Tutorials](../docs/v3/kloc-intelligence/tutorials/) | First time here — [installation](../docs/v3/kloc-intelligence/tutorials/installation.md) and a guided [first queries](../docs/v3/kloc-intelligence/tutorials/first-queries.md) walk-through |
| [How-To Guides](../docs/v3/kloc-intelligence/how-to-guides/) | Task recipes: [import + manage graph](../docs/v3/kloc-intelligence/how-to-guides/import-and-manage-graph.md), [analyze Symfony flows](../docs/v3/kloc-intelligence/how-to-guides/analyze-symfony-flows.md), [enrich with AI](../docs/v3/kloc-intelligence/how-to-guides/enrich-with-ai.md), [wire the MCP server](../docs/v3/kloc-intelligence/how-to-guides/mcp-server-setup.md), [Cypher queries](../docs/v3/kloc-intelligence/how-to-guides/direct-cypher-queries.md), [switch embedding providers](../docs/v3/kloc-intelligence/how-to-guides/switch-embedding-providers.md) |
| [Reference](../docs/v3/kloc-intelligence/reference/) | Exhaustive specs: [CLI](../docs/v3/kloc-intelligence/reference/cli-reference.md), [MCP server + tools](../docs/v3/kloc-intelligence/reference/mcp-server-reference.md), [graph schema](../docs/v3/kloc-intelligence/reference/graph-schema.md), [env vars](../docs/v3/kloc-intelligence/reference/environment-variables.md) |
| [Explanation](../docs/v3/kloc-intelligence/explanation/) | Concepts: [what it does](../docs/v3/kloc-intelligence/explanation/what-it-does.md), [how processes work](../docs/v3/kloc-intelligence/explanation/how-processes-work.md), [architecture decisions](../docs/v3/kloc-intelligence/explanation/architecture-decisions.md) |
| [Architecture](../docs/v3/kloc-intelligence/architecture/) | Diagrams: [system](../docs/v3/kloc-intelligence/architecture/system-architecture.md), [flow graph v3](../docs/v3/kloc-intelligence/architecture/flow-graph-design.md), [AI pipeline](../docs/v3/kloc-intelligence/architecture/ai-pipeline-architecture.md) |
| [Infrastructure](../docs/v3/kloc-intelligence/infrastructure/) | Ops: [Docker](../docs/v3/kloc-intelligence/infrastructure/docker-deployment.md), [performance + scaling](../docs/v3/kloc-intelligence/infrastructure/performance-scaling.md), [troubleshooting](../docs/v3/kloc-intelligence/infrastructure/troubleshooting.md) |

Migration note: until very recently these guides lived at `docs/usage/kloc-intelligence/`. The old paths are obsolete — use the v3 set above.

## Commands at a glance

| Group | Commands |
| --- | --- |
| Schema | `schema ensure` · `schema verify` · `schema reset` |
| Ingest | `import` · `import-flows` |
| Structural | `resolve` · `owners` · `usages` · `deps` · `context` · `inherit` · `overrides` · `flows` |
| Symfony entities | `messages` · `events` · `http-clients` |
| Source | `source` · `chunks` |
| AI | `enrich` · `enrich-status` · `enrich-flows` · `explain` · `search` |
| Server | `mcp-server` (stdio) · `mcp-server-http` (Streamable HTTP) |

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

# Symfony flow namespace filter (used by `import-flows`)
# Comma-separated FQN prefixes; default `App\`. Messages / events / http_clients
# are universal — this filter applies to :Flow entries only.
# KLOC_FLOW_NAMESPACES=App\,Domain\Orders\,Acme\

# Enrichment namespace deny-list (used by `enrich` / `enrich-status`)
# Comma-separated FQN prefixes to SKIP. Nodes are still imported into Neo4j
# but no LLM explanation is generated and nothing is embedded into Qdrant —
# so excluded nodes also never surface in `kloc search`. Default empty.
# Use this when sot.json includes vendor (Symfony\, Doctrine\, …) but you
# only want to pay LLM tokens on your own application code.
# KLOC_ENRICH_EXCLUDE_NAMESPACES=Symfony\,Doctrine\,Twig\,Psr\,Monolog\
```

Native Google Gemini works out of the box via the OpenAI-compatible endpoint
(`https://generativelanguage.googleapis.com/v1beta/openai/`); a compat shim in
`src/ai/_haystack_compat.py` handles Gemini's missing `usage` field. See
[switch-embedding-providers.md](../docs/v3/kloc-intelligence/how-to-guides/switch-embedding-providers.md)
and [environment-variables.md](../docs/v3/kloc-intelligence/reference/environment-variables.md)
for full provider recipes.

## Architecture

```
src/
├── cli.py              # Typer entry point — every CLI command
├── config.py           # Neo4jConfig from env
├── server/
│   ├── mcp.py          # MCP server core + stdio transport (JSON-RPC 2.0), 22 tools
│   └── mcp_http.py     # Streamable HTTP transport (Starlette + uvicorn, `http` extra)
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
uv sync --extra dev    # core deps are always installed; --extra dev adds tooling

# Lint + format check
uv run ruff check src tests benchmarks
uv run ruff format --check src tests benchmarks

# Static analysis
uv run mypy src

# Tests (unit + Neo4j integration; snapshots + flow-enricher tests need fixtures)
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

### Flow-enricher tests

`tests/test_flow_enricher.py` is skipped in CI by default because the Haystack
pipelines it imports are expensive to load. Run locally:

```bash
uv run pytest tests/test_flow_enricher.py -v
```

## MCP server

Two transports — same 22 tools, same `MCPServer` dispatch core.

### stdio (default, for editor MCP clients)

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

### Streamable HTTP (for remote clients, web UIs, n8n, agents-as-a-service)

```bash
uv run kloc-intelligence mcp-server-http              # 127.0.0.1:8765/mcp
uv run kloc-intelligence mcp-server-http --port 9000
uv run kloc-intelligence mcp-server-http --host 0.0.0.0 --port 8765  # LAN — trusted networks only
```

Single endpoint at `POST /mcp` accepts JSON-RPC 2.0 (single or batch) and
returns `application/json`. `GET /mcp` returns 405 (no server-initiated
streams); `DELETE /mcp` returns 204 (no server-side sessions). `GET /health`
returns a small JSON status object. Default bind is **localhost only** — set
`--host 0.0.0.0` deliberately, and only on networks where you control access.

Wire into clients that speak Streamable HTTP:

```json
{
  "mcpServers": {
    "kloc-intelligence": {
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

Quick smoke test from the shell:

```bash
curl -sS -X POST http://localhost:8765/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '.result.tools | length'
# => 22
```

#### Run it under Docker Compose

The top-level `docker-compose.yml` ships an opt-in `mcp-server` service that
bundles the HTTP daemon next to Neo4j and Qdrant. It's gated by the `mcp`
profile so the default `docker compose up -d` still brings only the DBs:

```bash
docker compose --profile mcp up -d --build

# Liveness
curl -s http://localhost:8765/health
# {"status":"ok","transport":"streamable-http"}

# tools/list
curl -sS -X POST http://localhost:8765/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '.result.tools | length'
```

The container picks `bolt://neo4j:7687` and `http://qdrant:6333` automatically.
LLM / embedding keys are passed through from your shell or `.env`
(`LLM_API_KEY`, `EMBEDDING_API_KEY`, etc.). The host port is bound to
`127.0.0.1:8765` by default — flip the mapping only behind a reverse proxy
or on a trusted private network. Override the host port via
`MCP_HTTP_PORT=9000 docker compose --profile mcp up -d`.

The 22 tools become callable as `mcp__kloc-intelligence__kloc_context`,
etc. See [reference/mcp-server-reference.md](../docs/v3/kloc-intelligence/reference/mcp-server-reference.md)
for the full tool catalog and [how-to-guides/mcp-server-setup.md](../docs/v3/kloc-intelligence/how-to-guides/mcp-server-setup.md)
for end-to-end wire-up.

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
