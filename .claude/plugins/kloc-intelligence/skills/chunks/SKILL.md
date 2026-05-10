---
description: Read PHP source for a node — either the full enclosing range or token-bounded chunks (the same chunks the embedder uses). Use when the user asks to see the actual code, wants to read a method body, needs an excerpt for LLM context, or wants to know how a class is chunked.
---

# kloc-intelligence — Source / chunks skill

Two commands, both read PHP source from disk using the file + line range
stored on the resolved node. The difference:

| Command | Returns | Use when |
| --- | --- | --- |
| `source` | The full enclosing range — one blob | You want to read the actual code |
| `chunks` | The same chunks the embedder produces | You want LLM-budget-friendly slices, or to see how a class was split |

Both require `KLOC_PROJECT_ROOT` (the path to the PHP project) — set
either as env var or via `--project-root`.

## `source` — raw read

### CLI

```bash
uv run kloc-intelligence source "OrderService::createOrder"
uv run kloc-intelligence source "App\\Entity\\Order" --json
uv run kloc-intelligence source "Foo" --project-root /tmp/other-project
```

Rich output prints `<fqn> (<kind>)` header, then the file + line range,
then the body. JSON output:

```json
{
  "node_id": "node:...",
  "kind": "Method",
  "fqn": "App\\Service\\OrderService::createOrder",
  "file": "src/Service/OrderService.php",
  "start_line": 24,
  "end_line": 47,
  "lines": 24,
  "chars": 812,
  "tokens_estimate": 203,
  "content": "<the actual PHP code>"
}
```

Line numbers are 1-based. `tokens_estimate` is `chars / 4` — rough but
enough for prompt-budget math.

### MCP — `kloc_source`

```json
{"symbol": "OrderService::createOrder", "project_root": "/optional/override"}
```

Same JSON shape as the CLI's `--json` output.

## `chunks` — embedder-shaped slices

`chunks` returns the **exact** chunks `enrich` feeds into Qdrant. Useful
for two things:

1. **LLM context budgeting** — if you need to fit a class into a
   limited context window, the same chunking the embedder uses is a
   pre-made answer.
2. **Debugging enrichment** — if a class isn't surfacing well in
   semantic search, look at how it was chunked.

### CLI

```bash
uv run kloc-intelligence chunks "OrderService"                       # default 8000-token budget
uv run kloc-intelligence chunks "OrderService" --max-tokens 4000     # tighter
uv run kloc-intelligence chunks "OrderService" --json
```

Output: one block per chunk with `chunk i/N (~N tokens, M chars)`
header, then the content.

### Chunking rules

| Node kind | Strategy |
| --- | --- |
| `Method` | Always one chunk. Truncated only if the method body exceeds `--max-tokens` (rare). |
| `Function` | Same as Method. |
| `Class` / `Interface` / `Trait` / `Enum` | Single chunk if the whole body fits in the budget. Otherwise split by **method boundary**: each chunk contains a class-context prefix (declaration line + property list + `// ... (N methods, chunked for embedding) ...` marker) plus a contiguous group of method bodies. |
| Other kinds | Single chunk, truncated if oversized. |

This means every method-containing chunk for a class still carries the
class declaration / properties as context, so the LLM/embedder doesn't
lose the "this method belongs to class X" signal.

### MCP — `kloc_chunks`

```json
{
  "symbol": "OrderService",
  "max_tokens": 8000,
  "project_root": "/optional/override"
}
```

Returns:

```json
{
  "node_id": "node:...",
  "kind": "Class",
  "fqn": "App\\Service\\OrderService",
  "file": "src/Service/OrderService.php",
  "max_tokens": 8000,
  "total_chunks": 3,
  "chunks": [
    {"index": 0, "total": 3, "tokens_estimate": 1850, "chars": 7402, "content": "..."},
    …
  ]
}
```

## Tips

- **`source` vs `chunks` for methods is the same** — methods are always
  one chunk. Prefer `source` for "show me this method".
- **For large classes, `chunks` is the only sane option** — one
  10K-line god class would blow any LLM context if read as a single
  blob.
- **`tokens_estimate` is rough** — `chars / 4`. Real tokens vary by
  tokenizer; use the value as a lower bound and add 20% headroom.
- **`KLOC_PROJECT_ROOT` is required** — if unset on an env-only run,
  pass `--project-root` per invocation. The graph stores file paths
  relative to this root.
- **If `Could not read source for X`** — the node has no `file`
  property (vendor symbols stripped to interface) or the file isn't
  under `KLOC_PROJECT_ROOT`. Check `kloc_resolve` first to confirm the
  node has a file.

## Suggested workflow

For agent-driven exploration:

```
context(X) → see relations
↓
chunks(X) → read X's body within a token budget
↓
context(callee from chunks) → drill into something X uses
↓
chunks(callee) → read its body
```

This is the same loop `enrich-flows` runs internally — context walk to
collect referenced nodes, chunks each, hand them to the LLM.
