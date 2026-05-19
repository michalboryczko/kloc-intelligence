# CLAUDE.md

Project-specific guidance for working in `kloc-intelligence`. Read this first
before making changes. For the user-facing overview see `README.md`.

## What this project is

`kloc-intelligence` is the **stateful**, Neo4j+Qdrant-backed query and AI
enrichment layer of the kloc pipeline. It sits at the end of:

```
PHP source → kloc-indexer-php → index.json → kloc-mapper → sot.json
                                                    ↘ kloc-symfony → symfony-kloc.json
                                                                      ↓
                                                              kloc-intelligence
                                                              (Neo4j + Qdrant + CLI + MCP)
```

Compared to its sibling `kloc-cli` (stateless, parses `sot.json` per
invocation), this one persists the graph and adds multi-hop Cypher, LLM
explanations, and semantic search.

This repo lives inside the monorepo at `/Users/michal/dev/ai/kloc/`. Sibling
sub-repos: `kloc-cli`, `kloc-mapper`, `kloc-indexer-php`, `kloc-symfony`,
`kloc-reference-project-php`.

## Where things are

### Source code (`src/`)
| Path | What lives there |
| --- | --- |
| `src/cli.py` | Typer entry point. 24 commands (3 schema + 21 top-level, incl. `messages`/`events`/`http-clients`). Loads `.env` itself without overriding shell env. |
| `src/config.py` | `Neo4jConfig.from_env()` — single source of truth for Neo4j env wiring. |
| `src/server/mcp.py` | MCP JSON-RPC 2.0 server core + stdio transport. 22 tools (`kloc_resolve`, `kloc_context`, …, plus `kloc_messages`/`kloc_message`/`kloc_events`/`kloc_event`/`kloc_http_clients`/`kloc_http_client`). `MCPServer.handle_jsonrpc()` is the single dispatch entry point — both transports use it. |
| `src/server/mcp_http.py` | Streamable HTTP transport (MCP 2025-03-26). Starlette + uvicorn, lazy-imported, gated by the `http` extra. POST `/mcp` accepts JSON-RPC single/batch and returns `application/json`; GET returns 405 (no server-initiated streams); DELETE returns 204 (stateless). `GET /health` for liveness. Default bind 127.0.0.1:8765 — only flip to 0.0.0.0 on trusted networks. |
| `src/db/connection.py` | Thin `neo4j` driver wrapper (`Neo4jConnection`). |
| `src/db/query_runner.py` | Cypher executor with logging. Thread-safe. |
| `src/db/schema.py` | `NODE_KINDS` (13), `EDGE_TYPES` (13), `INDEXES` (16 incl. Message/Event/HttpClient), `CONSTRAINTS` (incl. `:Flow.flow_id`, `:Message.id`, `:Event.id`, `:HttpClient.id` uniqueness). Touch this when the schema actually changes. |
| `src/db/importer.py` | `sot.json` → Neo4j (msgspec parsing, batched MERGE). |
| `src/db/flow_importer.py` | `symfony-kloc.json` v3 → `:Flow` / `:Message` / `:Event` / `:HttpClient` via MERGE-reconcile. Preserves `:Flow.explanation` across re-imports; sweeps legacy `FLOW_TRIGGERS`; prunes orphan flow embeddings by `flow_id` filter via `flow_qdrant.delete_flow_embedding`. |
| `src/db/queries/` | One module per structural query (`resolve`, `usages`, `deps`, `context_class`, `context_method`, `context_interface`, `context_property`, `context_value`, `context_class_uses`, `definition`, `inherit`, `overrides`, `owners`, `flows`, `helpers`). |
| `src/orchestration/` | Kind-based tree builders. `context.py` dispatches on `kind` to `class_context.py` / `interface_context.py` / `method_context.py` / `property_context.py` / `value_context.py` / `generic_context.py`. `usages.py` / `deps.py` / `simple.py` for the simpler shapes. |
| `src/logic/` | Pure logic: `handlers.py`, `reference_types.py`, `graph_helpers.py`, `polymorphic.py`, `definition.py`. No I/O. |
| `src/models/` | `node.py` (`NodeData`), `results.py` (in-memory result dataclasses), `output.py` (contract-compliant `ContextOutput` with 1-based lines + camelCase). |
| `src/ai/config.py` | `AIConfig` + `LLMProviderConfig` + `EmbeddingProviderConfig` — the two providers are **independent**. |
| `src/ai/pipelines.py` | Haystack pipelines (`explain`, `embed`, `search`, `flow`). Per-thread instances — Haystack `Pipeline` is **not thread-safe**. |
| `src/ai/_haystack_compat.py` | Tolerant embedders that handle Gemini's missing `usage` field. |
| `src/ai/_parallel.py` | `ThreadPoolExecutor` runner + `ThreadLocalPipelines[T]` for concurrent enrichment. `QueryRunner` is thread-safe; `Pipeline` is not. |
| `src/ai/enricher.py` | Class/Method enrichment orchestrator. Drives `_parallel.py`. |
| `src/ai/flow_enricher.py` | `:Flow` business-process summary orchestrator. Gathers v3 dispatch context (`emits_messages`, `emits_events`, `http_calls`, `triggered_by_messages`, `triggered_by_events`) and passes it as named kwargs to `run_explain_flow` — that call is the single test seam, don't pre-render upstream. |
| `src/ai/flow_qdrant.py` | Per-flow Qdrant filter-delete (`delete_flow_embedding`) + symmetric scroll-based inspection helper (`list_flow_point_ids`). NEVER calls `delete_collection`. |
| `src/ai/chunker.py` | Token-bounded chunking for large classes. |
| `src/ai/source_reader.py` | File + line-range source reader. |
| `src/output/` | Rich console (`console.py`) + JSON (`json_formatter.py`) formatters. Flow / message / event / http-client renderers live in `output/flows.py`. |

### Tests, docs, infra
| Path | Notes |
| --- | --- |
| `tests/conftest.py` | `loaded_database` fixture (App-only) and `loaded_database_with_vendor` fixture (vendor-inclusive) with path-aware caching. Both `pytest.skip` when fixtures aren't present. |
| `tests/test_snapshot.py` | Loads vendor-inclusive dataset, compares orchestration output against monorepo golden at `../tests/snapshot-2103260323.json`. **Skipped in CI.** |
| `tests/test_flow_enricher.py` | Needs `--extra ai`. Skipped in CI by default. |
| `tests/snapshots/` | Inline snapshot fixtures for non-test_snapshot tests. |
| `docs/specs/` | Feature specs and plans (e.g. `paraller-llm-api.md`, `paraller-llm-api-plan.md`, `kloc-intelligence/`). |
| `docs/MIGRATION.md` | kloc-cli → kloc-intelligence migration guide. |
| `bin/` | `setup.sh`, `import.sh`, `reset.sh`, `status.sh` — convenience wrappers. |
| `docker/` | `Dockerfile` (kloc-intelligence runtime image — installs `--extra http --extra ai`, default `CMD` is `mcp-server-http --host 0.0.0.0 --port 8765`), embedded `docker-compose.yml`, `neo4j.conf`. Top-level `docker-compose.yml` is the canonical one. |
| `docker-compose.yml` | Neo4j 5 community + Qdrant v1.12.1 with named volumes, plus an opt-in `mcp-server` service (`profiles: ["mcp"]`) that runs the Streamable HTTP MCP daemon. Bring up DBs only with `docker compose up -d`; bring up the daemon too with `docker compose --profile mcp up -d`. |
| `.github/workflows/ci.yml` | CI: lint + format + mypy + pytest. Neo4j and Qdrant come up as service containers. |

### Outside this repo
| Path | What it is |
| --- | --- |
| `../docs/v3/kloc-intelligence/` | **Canonical user-facing docs** (Diátaxis: tutorials / how-to-guides / reference / explanation / architecture / infrastructure). Start at `../docs/v3/kloc-intelligence/index.md`. The pre-v3 `../docs/usage/kloc-intelligence/` set is obsolete — don't link to it. |
| `../artifacts/kloc-dev/context-final/sot.json` | App-only fixture (1154 nodes, 825 KB). Used by `loaded_database`. |
| `../artifacts/kloc-dev/context-rust-internal/sot.json` | Vendor-inclusive fixture (128K nodes, 92.8 MB). Used by `loaded_database_with_vendor` and snapshot tests. |
| `../tests/snapshot-2103260323.json` | Golden snapshot, lives in **monorepo parent** (separate git repo from this one). |
| `../kloc-contracts/` | JSON schemas — `sot-json.json`, etc. Output must conform. |
| `../kloc-mapper/src/models.py` | Canonical `NodeKind` / `EdgeType` enums (13 each). |

## Common tasks

```bash
# from kloc-intelligence/
uv sync --all-extras                 # full install (dev + ai + http)
docker compose up -d                 # Neo4j 5 + Qdrant
docker compose --profile mcp up -d   # …plus MCP HTTP daemon on 127.0.0.1:8765

# Lint / format / typecheck
uv run ruff check src tests benchmarks
uv run ruff format --check src tests benchmarks
uv run mypy src

# Tests (skip snapshot + flow_enricher in default run)
uv run pytest -q --deselect tests/test_snapshot.py --deselect tests/test_flow_enricher.py

# Snapshot tests (need fixtures at ../artifacts/kloc-dev/)
uv run pytest tests/test_snapshot.py -v

# Run a CLI command
uv run kloc-intelligence schema verify
uv run kloc-intelligence context 'App\Service\OrderService::createOrder'
```

`pyproject.toml` mypy block has a list of modules currently in `ignore_errors`
— typing is advisory until those modules get cleaned. Don't fight mypy for
modules in that list.

## Pipeline order (must respect)

```
schema ensure → import sot.json → import-flows symfony-kloc.json
                                   ↓
                          enrich (Class/Method) → enrich-flows (:Flow)
```

Skipping `import-flows` is fine; the rest of the pipeline still works.
`enrich` writes `node.explanation`, embeddings to `code_embeddings` +
`explain_embeddings`. `enrich-flows` writes `flow.summary` + embeddings to
`flow_explain_embeddings`. `search` queries all three collections.

## Graph schema cheat-sheet

13 node kinds: `Class`, `Interface`, `Trait`, `Enum`, `Method`, `Function`,
`Property`, `Const`, `EnumCase`, `Argument`, `Value`, `Call`, `File`.

13 edge types: `contains`, `uses`, `extends`, `implements`, `overrides`,
`type_hint`, `calls`, `receiver`, `argument`, `produces`, `assigned_from`,
`type_of`, `return_type`.

Plus the Symfony-flow subgraph (v3 schema — `FLOW_TRIGGERS` is gone):
- Nodes: `:Flow`, `:Message`, `:Event`, `:HttpClient`
- Edges:
  - `:Flow -[FLOW_ENTRY]-> :Method` and `:Flow -[FLOW_ENTRY_CLASS]-> :Class`
  - `:Flow -[EMITS]-> :Message|:Event` (with `caller_method_fqn`, `call_node_id`)
  - `:Call -[EMITS]-> :Message|:Event` (call-site mirror — same call_node_id; preserves duplicate dispatches)
  - `:Flow -[USES_HTTP_CLIENT]-> :HttpClient` plus the `:Call -[USES_HTTP_CLIENT]-> :HttpClient` mirror
  - `:Message|:Event -[HANDLED_BY]-> :Flow` (events carry `priority`)
  - `:Message|:Event|:HttpClient -[OF_TYPE]-> :Class` (optional — absent for vendor classes like the PayPal HTTP client)
- Re-import is idempotent. `:Flow.explanation`, `.explain_model`, `.explain_at` are NEVER overwritten by the importer. Orphan flow embeddings are filter-deleted from the `flow_explain_embeddings` Qdrant collection by `flow_id`; the collection itself is never dropped.

`:Node` is a label every symbol carries; specialized labels (`:Class`,
`:Method`, …) are stacked. Use `:Node` for cross-kind queries; use specific
labels for kind-scoped ones (faster, hits the kind-specific index).

## Gotchas / patterns learned the hard way

- **Cypher doesn't guarantee result order** without `ORDER BY`. When the
  output is sorted in Python, the sort key must include enough tiebreakers
  to be deterministic. Standard tiebreaker for entry sorts:
  `(e.file or "", e.line if e.line is not None else 0, e.fqn or "")`. The
  `fqn` final key was added in commit `6bdd858` across 12 sort sites.
- **Haystack `Pipeline` is not thread-safe.** `_parallel.py` uses a
  `ThreadLocalPipelines[T]` so each worker thread gets its own pipeline
  instance. `QueryRunner` IS thread-safe.
- **Per-slot accessor pattern** (`_get_pipelines()` in `enricher.py`):
  prefer instance attribute when present (tests inject mocks via
  attribute), fall back to thread-local. Don't pre-construct
  thread-locals on `__init__`.
- **Two-fixture pattern**. `loaded_database` (App-only) is the default for
  most tests. `loaded_database_with_vendor` (128K vendor-inclusive nodes)
  is only for snapshot tests. The path-aware cache in `conftest.py`
  reloads when a test asks for a different dataset — don't bypass it.
- **`context-final` vs `context-rust-internal`** are the two canonical
  upstream-`kloc-cli` dataset variants. `context-rust-internal` is built
  with `--internal-all` and includes vendor packages.
- **`.env` loading**: `src/cli.py` reads `.env` manually without
  overriding existing shell env. Shell exports (`LLM_API_KEY` in `.zshrc`)
  win over `.env`. Don't introduce `python-dotenv` — it inverts that.
- **Output is contract-bound.** `src/models/output.py` produces 1-based
  line numbers and camelCase keys to match the `kloc-contracts` schemas.
  Snapshot tests + `tests/test_contract.py` enforce this. Internal models
  (`results.py`) use 0-based / snake_case — convert at the boundary.
- **`e.target AS edge_target` in `context_class_uses.py` is a known
  no-op leftover** — `target` isn't a Neo4j edge property; sot.json's
  `source`/`target` become the relationship endpoints, not properties.
  Returns NULL silently. Harmless cleanup candidate.

## When working on AI features

- LLM and embedding providers are configured **independently**. Default
  for both is OpenRouter. Gemini works natively via the OpenAI-compatible
  endpoint plus `_haystack_compat.py`.
- Concurrency is env-driven: `ENRICH_CONCURRENCY` (default 10) for
  `enrich`, `ENRICH_FLOWS_CONCURRENCY` (default 10) for `enrich-flows`.
- Symfony :Flow namespace filtering is env-driven: `KLOC_FLOW_NAMESPACES`
  (comma-separated FQN-prefix allow-list, default `App\`). Loaded by
  `flow_importer.load_flow_namespaces()`; consumed by `parse_v3`. Applies
  to :Flow entries only — messages / events / http_clients are universal.
- Gemini embedding dimension is 3072, OpenRouter qwen3-embedding-8b is
  4096. Switching providers means dropping the Qdrant collections
  (different dimensions can't coexist in one collection).

## When working on snapshot tests

- The golden snapshot file lives in the **monorepo parent**
  (`../tests/snapshot-2103260323.json`), not in this repo. Regenerating
  it means committing in the parent repo separately.
- Snapshot fixture is `loaded_database_with_vendor` (vendor-inclusive)
  per `cases.json: sot_id = "context-rust-internal"`.
- If output isn't deterministic, the sort key in the orchestrator is
  missing a tiebreaker. Check that every `*.sort(key=...)` ends with
  `e.fqn or ""` (or whatever stable identifier the entry has).

## What to avoid

- Don't add backwards-compat shims for the kloc-cli interface — the two
  tools share a JSON contract, not an API.
- Don't introduce abstractions over the Haystack pipeline that hide its
  thread-unsafety. `_parallel.py` is the boundary.
- Don't move the `.env` loader into a third-party library — the
  "don't-override-shell-env" semantics are deliberate.
- Don't commit fixtures (`artifacts/`, `.env`, golden snapshots in this
  repo). The monorepo parent owns those.
