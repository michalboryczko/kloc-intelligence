---
description: Semantic search across PHP code, LLM explanations, and Symfony flow summaries using natural-language queries. Embeds the query and ranks by cosine similarity across three Qdrant collections, dedupes by node, returns the top hits. Use when the user asks "find me code that does X", "what handles Y in business terms", "where do we charge customers", "is there a place that validates orders", or any question framed in business vocabulary rather than class/method names.
---

# kloc-intelligence — Search skill

`search` is the "I don't know the symbol name, but I know what I want"
escape hatch. It embeds the query and looks up nearest neighbors in
**three** Qdrant collections:

| Collection | What's in it | Populated by |
| --- | --- | --- |
| `code_embeddings` | Source-code chunks of Class/Method nodes | `enrich` |
| `explain_embeddings` | LLM-authored functional descriptions | `enrich` |
| `flow_explain_embeddings` | Flow business-process summaries | `enrich-flows` |

All three are queried by default and merged into one ranked list. Each
hit reports which collection it came from.

## CLI

```bash
uv run kloc-intelligence search "validate order before checkout"
uv run kloc-intelligence search "places where we charge a customer" --limit 5
uv run kloc-intelligence search "process customer orders" --collection both
```

Flags:

| Flag | Default | What it does |
| --- | --- | --- |
| `--limit` / `-l` | 10 | Max hits returned |
| `--collection` / `-c` | `both` | `code` / `explain` / `both` — narrow to one |
| `--json` / `-j` | off | Machine-readable output |

Note: `--collection both` is the **default** and despite the name it now
queries all three collections (`code_embeddings`, `explain_embeddings`,
`flow_explain_embeddings`), then dedupes by `node_id`. `code` queries
only `code_embeddings`; `explain` only `explain_embeddings`.

## MCP — `kloc_search`

```json
{
  "query": "validate order before checkout",
  "limit": 10,
  "collection": "both"
}
```

Returns:

```json
{
  "query": "...",
  "hits": [
    {
      "score": 0.87,
      "kind": "Method" | "Class" | "Flow",
      "fqn": "App\\Service\\OrderValidator::validate",
      "file": "src/Service/OrderValidator.php",
      "node_id": "node:...",
      "collection": "explain_embeddings",
      "name": "validate",
      "project": "default",
      "chunk_index": 0
    },
    …
  ]
}
```

## How ranking works

1. The query string is embedded by the same model used during enrichment
   (`EMBEDDING_MODEL`).
2. For each enabled collection, Qdrant computes cosine similarity
   between the query vector and every stored point.
3. Top-`limit` per collection are concatenated.
4. Duplicates (same `node_id` across collections) are collapsed —
   highest score wins.
5. The final list is sorted by score descending and trimmed to `limit`.

## Reading the score

Cosine similarity, range 0..1.

| Range | What it usually means |
| --- | --- |
| `> 0.7` | Strong match — same intent, often the right answer |
| `0.5–0.7` | Plausible match — same domain, may require a second look |
| `0.3–0.5` | Loose match — same vocabulary, different concept |
| `< 0.3` | Unrelated — usually noise |

Score is most comparable **within** a query, not across queries — a
0.65 hit on one query may be the best you'll get, while another query
might have 0.85 hits.

## Which collection should you trust?

| Collection | Best for |
| --- | --- |
| `code_embeddings` | "Find me the actual code that does X" — embeds the source, so query phrasing close to code wins |
| `explain_embeddings` | "What's a method that's responsible for X" — embeds the LLM's description, so business-vocabulary queries win |
| `flow_explain_embeddings` | "Which endpoint / handler does X" — flow-level granularity |

Default `--collection both` lets the score sort it out. Use a narrow
`--collection` only when you know which surface you want.

## When to use `search` vs `context`

| Use `search` when | Use `context` when |
| --- | --- |
| You don't know the symbol name | You know the symbol (or have a hit from search) |
| The question is "where" / "is there" | The question is "what / how / why" |
| You want recall over precision | You want the call tree |

The standard pattern is: **search → resolve → context**.

```bash
# 1. Find a candidate
uv run kloc-intelligence search "create a new customer order"
# top hit: App\Service\OrderService::createOrder

# 2. Confirm + get exact FQN
uv run kloc-intelligence resolve "OrderService::createOrder"

# 3. Read the call tree
uv run kloc-intelligence context "OrderService::createOrder" --depth 3 --impl
```

## Prerequisites

- `EMBEDDING_API_KEY` set in `.env` (otherwise `search` aborts with a
  clear error)
- Qdrant up + reachable at `QDRANT_URL`
- The collection(s) you're querying must have data —
  `code_embeddings` + `explain_embeddings` come from `enrich`,
  `flow_explain_embeddings` from `enrich-flows`

If `search` returns zero hits even on simple queries: confirm the
collections aren't empty. If you switched embedding models, the old
collection's vectors are useless — drop and re-`enrich`.

## Tips for good queries

- **Business vocabulary works better against `explain_embeddings`** —
  "places where we charge a customer" rather than "Stripe::charge".
- **Code-shape phrasing works better against `code_embeddings`** —
  "function that loops over orders and calls process" rather than
  "process orders in bulk".
- **Flow queries hit `flow_explain_embeddings`** — "API endpoint for
  placing an order" matches the surface lines in flow summaries.
- **Short queries score higher** than long ones in many embedding
  models. If a long query doesn't return good results, try the core
  phrase alone.
