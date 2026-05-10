---
description: Discover and explore Symfony application flows — HTTP endpoints, message handlers, event subscribers, CLI commands. List flows, get full detail with business-process summaries, find flows by partial name, follow FLOW_TRIGGERS chains, and search flows semantically. Use when the user asks "what endpoints does this app have", "what runs when this event fires", "find the flow that creates orders", "what does flow X do", or anything about Symfony routes / handlers / subscribers / commands.
---

# kloc-intelligence — Flows skill

A **flow** is a framework-level entry point — the outermost boundary
where the application starts doing work. In a Symfony app there are
four kinds, all stored as `:Flow` nodes with a `type` property:

| Type | Surface | Examples |
| --- | --- | --- |
| `http` | HTTP route + verb | `POST /api/orders`, `GET /api/customers/{id}` |
| `message` | Symfony Messenger handler | handler for `OrderCreatedMessage` |
| `event` | EventDispatcher subscriber | subscriber for `OrderPlacedEvent` |
| `cli` | Symfony Console command | `app:process-orders` |

Each flow has:
- `flow_id` — stable identifier like `flow:http:App\Ui\Rest\Controller\OrderController::create`
- `entry_fqn` — the entry class FQN, e.g. `App\Ui\Rest\Controller\OrderController`
- `entry_method` — the method name, e.g. `create`
- One `FLOW_ENTRY` edge → the Method node (where execution starts)
- Zero or more `FLOW_TRIGGERS` edges → flows it dispatches to (one flow's
  HTTP handler dispatches a message that another flow's handler consumes)
- (After `enrich-flows`) an LLM-authored business-process summary

## CLI

### List

```bash
uv run kloc-intelligence flows                               # all
uv run kloc-intelligence flows --type http                   # filter
uv run kloc-intelligence flows --type http,cli               # multi-filter
uv run kloc-intelligence flows --json                        # machine-readable
```

Output is a Rich table: type / name / entry FQN / flow_id.

### Detail (single flow)

```bash
uv run kloc-intelligence flows flow:http:App\\...::create     # exact flow_id
uv run kloc-intelligence flows OrderController::create        # partial match → detail (if unique)
uv run kloc-intelligence flows OrderController                # partial match → candidates (if N>1)
```

Detail mode prints:
- header — name + type + flow_id
- entry — Method FQN + file:start-end
- type-specific row — route + HTTP methods, or message class, or event name, or command name
- **Summary** — the business-process line if `enrich-flows` has run
- triggers in/out — flows that trigger this one, and flows this one triggers

### JSON shape

```json
{
  "mode": "list" | "detail" | "candidates",
  ...
}
```

Discriminated union — branch on `mode`:
- `list` → `{"flows": [...]}`
- `detail` → `{"flow": {entry, triggers_in, triggers_out, explanation, ...}}`
- `candidates` → `{"candidates": [{flow_id, type, name, entry_fqn}, ...]}`

## MCP — `kloc_flows`

Same surface, exposed as one tool with optional inputs:

```json
{"flow_id": "...", "type": "http,cli"}
```

- Omit both → list all flows
- `type` alone → filtered list
- `flow_id` → exact lookup or partial match (candidates if multi-match)

`kloc_enrich_flows` is the trigger to (re)generate summaries:

```json
{"force": false}
// returns: {"total", "processed", "skipped", "failed", "failed_flows"}
```

## Search flows semantically

After `enrich-flows`, every flow's business-process summary lives in the
`flow_explain_embeddings` Qdrant collection. `search` queries all 3
collections and returns flows ranked alongside Class/Method hits.

```bash
uv run kloc-intelligence search "create a new customer order"
uv run kloc-intelligence search "places where we charge a customer"
uv run kloc-intelligence search "notify users about failed deliveries"
```

Each hit's `collection` field is one of `code_embeddings` /
`explain_embeddings` / `flow_explain_embeddings` — flow hits will say
the latter and their `kind` will be `Flow`.

See the `search` skill for ranking + scoring details.

## Using a flow with context

A flow's entry method is the natural seed for a full context walk. Get
the entry FQN from `flows <flow_id>`, then ask context how the work
fans out:

```bash
# 1. find the flow
uv run kloc-intelligence flows OrderController::create

# 2. read its entry method's call tree, polymorphic impls included
uv run kloc-intelligence context "OrderController::create" --depth 3 --impl
```

This pattern (flow → entry method → context with --impl + --depth=3) is
exactly what `enrich-flows` runs internally to assemble the LLM prompt.

See the `context` skill for how to read context output.

## Suggested explore loop

1. **Get the lay of the land** — `flows` (or `kloc_flows`) lists every
   entry point.
2. **Hone in** — `flows OrderController` if you remember a partial name,
   or `search "..."` if you only know the business intent.
3. **Read the summary** — detail mode shows the business-process line
   (after `enrich-flows`).
4. **Drop into structure** — `context <entry_fqn> --depth 3 --impl` for
   the full call tree.

If summaries are missing: run `enrich-flows`. If `flows` returns
nothing: confirm `import-flows` was run for this project.
