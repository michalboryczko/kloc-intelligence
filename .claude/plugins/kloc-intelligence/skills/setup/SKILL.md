---
description: Set up kloc-intelligence from scratch — install deps, choose an LLM/embedding provider, configure `.env`, bring up Neo4j + Qdrant via Docker, and create the graph schema. Use when the user mentions install, setup, env, .env, configure provider, OpenRouter, Gemini, Docker compose, Neo4j, Qdrant, or schema ensure.
---

# kloc-intelligence — Setup skill

This skill walks the user through bringing kloc-intelligence online from a
clean checkout. There are three things to land before anything else works:

1. **Python deps** installed (with the `ai` extra for enrich/search).
2. **Provider credentials** in `.env` (LLM + embedding URLs/keys/models).
3. **Infra running** — Neo4j 5 + Qdrant 1.12 via `docker compose up -d`,
   then `schema ensure` to create constraints and indexes.

## Step 1 — Install

```bash
uv sync --all-extras    # installs ai extras (haystack-ai, qdrant)
```

Without `--all-extras` the structural commands still work, but
`enrich` / `explain` / `search` / `enrich-flows` will refuse with
"AI features require additional dependencies".

## Step 2 — Configure providers (`.env`)

LLM and embedding providers are **independent** — different URLs / keys /
models. Both default to OpenRouter.

| Variable | What it does |
| --- | --- |
| `LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL` | Chat model for explain / enrich / enrich-flows |
| `EMBEDDING_API_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | Embedding model for enrich / enrich-flows / search |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | Bolt endpoint + creds |
| `QDRANT_URL` / `QDRANT_API_KEY` | Qdrant endpoint |
| `KLOC_PROJECT_ROOT` | Path to the PHP project (needed for `source`, `chunks`, `enrich*`) |

### Provider recipes

**OpenRouter (default — single key for both surfaces)**

```ini
LLM_API_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-...
LLM_MODEL=minimax/minimax-m2.7

EMBEDDING_API_URL=https://openrouter.ai/api/v1
EMBEDDING_API_KEY=sk-or-v1-...
EMBEDDING_MODEL=qwen/qwen3-embedding-8b
EMBEDDING_DIMENSION=4096
```

**Google Gemini (native, OpenAI-compat endpoint)**

```ini
LLM_API_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_API_KEY=<gemini-key>
LLM_MODEL=gemini-3-flash-preview

EMBEDDING_API_URL=https://generativelanguage.googleapis.com/v1beta/openai/
EMBEDDING_API_KEY=<gemini-key>
EMBEDDING_MODEL=gemini-embedding-001
EMBEDDING_DIMENSION=3072
```

Gemini's OpenAI-compat embeddings endpoint omits `usage`. The compat
shim at `src/ai/_haystack_compat.py` handles that — no extra work needed.

**Mixed** (e.g. Gemini chat + OpenRouter embeddings) — just set the LLM_*
group to Gemini and the EMBEDDING_* group to OpenRouter.

### Dimension changes

If you switch embedding models with a different dimension (e.g. 4096 →
3072), **drop the Qdrant collections first** or `enrich` will throw a
dimension-mismatch error.

```bash
uv run python -c "
from qdrant_client import QdrantClient
c = QdrantClient(url='http://localhost:6333')
for n in ('code_embeddings','explain_embeddings','flow_explain_embeddings'):
    try: c.delete_collection(n)
    except: pass
"
```

## Step 3 — Bring up infra + schema

```bash
docker compose up -d                                # Neo4j (7687/7474) + Qdrant (6333/6334)
uv run kloc-intelligence schema ensure              # constraints + 13 indexes
uv run kloc-intelligence schema verify              # confirm
```

If Neo4j fails to start: bump `NEO4J_HEAP_MAX` in `docker-compose.yml`
(default 2g; >500K nodes wants 4–8g).

## Validation checklist

Run these to confirm setup is healthy:

```bash
docker compose ps                              # both services Up + healthy
uv run kloc-intelligence schema verify         # constraints >= 1, indexes >= 13
uv run kloc-intelligence resolve "Foo"         # should print "No matches" (graph empty, but the connection works)
```

If `schema verify` errors with `Cannot connect to Neo4j at <uri>`:
- check `docker compose ps` — Neo4j may still be starting (takes ~30s)
- check `NEO4J_URI` matches the host port (default `bolt://localhost:7687`)
- check `NEO4J_PASSWORD` matches `docker-compose.yml`

## Next

Once setup is green, point the user at `/kloc-intelligence:ingestion` to
load their first sot.json and (optionally) symfony-kloc.json.

Detailed reference: `docs/usage/kloc-intelligence/configuration.md`.
