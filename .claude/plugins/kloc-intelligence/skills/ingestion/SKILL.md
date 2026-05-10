---
description: Run the kloc-intelligence ingestion pipeline — import sot.json into Neo4j, optionally import Symfony flows from symfony-kloc.json, then enrich Class/Method nodes and :Flow nodes with LLM explanations and Qdrant embeddings. Use when the user mentions import, ingest, sot.json, symfony-kloc.json, enrich, embeddings, flows import, reindex, or "load my project".
---

# kloc-intelligence — Ingestion skill

The pipeline is four steps, each independent and idempotent:

```
1. schema ensure       (one-time per environment)
2. import sot.json     (mandatory — the structural graph)
3. import-flows        (Symfony projects only — adds :Flow nodes + edges)
4. enrich              (optional — AI explanations + embeddings on Class/Method)
5. enrich-flows        (optional — flow business-process summaries)
```

Run in order. Each step assumes the previous landed.

## Step 1 — Schema (one-time)

```bash
uv run kloc-intelligence schema ensure        # idempotent — safe to repeat
```

If you need a clean slate: `schema reset` drops **all** Neo4j data and
recreates indexes/constraints. Use sparingly.

## Step 2 — Import `sot.json`

The Source of Truth file is produced by the kloc pipeline
(`kloc-indexer-php → kloc-mapper → sot.json`).

```bash
uv run kloc-intelligence import /path/to/sot.json
```

Default behavior: clears existing Neo4j data first, then imports nodes
(13 kinds) + edges (13 types), then validates the count. ~1 s per 100K
nodes on a default heap.

Flags:
- `--no-clear` — layer onto existing data instead of replacing
- `--no-validate` — skip the post-import counts check

## Step 3 — Import Symfony flows (optional)

If `symfony-kloc.json` was produced by `kloc-symfony`:

```bash
uv run kloc-intelligence import-flows /path/to/.kloc/symfony-kloc.json
```

This adds `:Flow` nodes (one per HTTP route, message handler, event
subscriber, CLI command) and joins them to the structural graph via
`FLOW_ENTRY` (Flow → Method) and `FLOW_TRIGGERS` (Flow → Flow). Always
clears existing flows first — flows are deterministic from the input
file. App-namespace filter: only flows with `App\` FQNs are imported;
vendor/framework flows are dropped.

After: `flows` should list them.

```bash
uv run kloc-intelligence flows                # listing
```

## Step 4 — Enrich nodes (optional, AI)

For each Class / Method node, walks one-hop context (parent classes,
argument types, first-level usages), asks the LLM for a 2-5 sentence
description, writes it to `n.explanation`, then embeds both the source
and the explanation into Qdrant (`code_embeddings` + `explain_embeddings`).

```bash
uv run kloc-intelligence enrich                          # all Class + Method
uv run kloc-intelligence enrich --kinds Class            # only classes
uv run kloc-intelligence enrich --force                  # re-enrich existing
uv run kloc-intelligence enrich --batch-size 5           # if rate-limited
uv run kloc-intelligence enrich --debug                  # log LLM prompts
```

Cost: 1 LLM call + 2 embedding calls per node. Reference project (~165
nodes) is single-digit cents. A 5K-method codebase ≈ $1–$5.

Progress: `enrich-status` shows per-kind enriched/pending counts.
Idempotent without `--force` — already-enriched nodes are skipped.

## Step 5 — Enrich flows (optional, Symfony + AI)

For each `:Flow`, walks depth-3 bidirectional context (with implementation
expansion) of the entry method, attaches source from referenced nodes
(callers/callees/types/impls), and asks the LLM for a structured 3-part
summary:

```
<Surface line — "API endpoint on path X method Y" / "Message handler for Z" / …>

<2-3 sentence behavior in business vocabulary>

<Optional downstream effects line>
```

```bash
uv run kloc-intelligence enrich-flows                    # process all
uv run kloc-intelligence enrich-flows --force            # regenerate
```

Stored as `f.explanation` on the Flow and embedded into the
`flow_explain_embeddings` collection so `search` returns flows.

## Quick end-to-end recipe

For a fresh PHP project:

```bash
export KLOC_PROJECT_ROOT=/path/to/php-project

uv run kloc-intelligence schema reset                     # clean slate
uv run kloc-intelligence import /path/to/sot.json
uv run kloc-intelligence import-flows /path/to/.kloc/symfony-kloc.json
uv run kloc-intelligence enrich
uv run kloc-intelligence enrich-flows
```

~15 min for the reference project; LLM time dominates.

## Reset matrix

| Goal | Run |
| --- | --- |
| Re-import the whole graph | `schema reset` then `import` |
| Regenerate one node's explanation | `explain <fqn> --force` |
| Regenerate all explanations + embeddings | `enrich --force` |
| Regenerate all flow summaries | `enrich-flows --force` |
| Drop AI data, keep graph | manually delete Qdrant collections |

## MCP equivalents

All five steps are exposed as MCP tools — `kloc_import`,
`kloc_import_flows`, `kloc_enrich`, `kloc_enrich_flows`. Use these when
an agent needs to trigger a re-ingest mid-session.

Detailed reference: `docs/usage/kloc-intelligence/data-setup.md`.
