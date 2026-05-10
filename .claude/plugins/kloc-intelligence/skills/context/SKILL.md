---
description: Get bidirectional context for a PHP symbol — what uses it (USED BY), what it uses (USES), with depth, polymorphic implementations, member references, and call arguments. The richest single query in kloc-intelligence. Use when the user asks "what calls this", "what does this call", "show me the call tree", "trace this method", "what depends on X", "what does Y depend on", or wants to read code top-down before changing it.
---

# kloc-intelligence — Context skill

`context` is the most-used command. It resolves a symbol then produces a
single tree-shaped result with **four** sections:

```
target → resolved symbol (file, line, signature)
definition → structural metadata (methods, fields, extends/implements, …)
used_by → list of callers (incoming USES, BFS by --depth)
uses → list of callees + types + impls (outgoing USES + execution flow)
```

The kind of the target determines how each section is built. See the
"How to read output" section below for what each field means.

## CLI

```bash
uv run kloc-intelligence context "OrderService::createOrder"          # method, depth=1
uv run kloc-intelligence context "App\\Entity\\Order" --depth 3       # class, deeper
uv run kloc-intelligence context "OrderRepositoryInterface" --impl    # interface + concrete impls
uv run kloc-intelligence context "OrderService::createOrder" --json   # machine-readable
```

Flags:

| Flag | Default | What it does |
| --- | --- | --- |
| `--depth` / `-d` | 1 | BFS depth — depth=1 is direct callers/callees; depth=3 expands further |
| `--limit` / `-l` | 100 | Cap on entries per direction (used_by / uses) |
| `--impl` / `-i` | off | Expand polymorphic implementations (interface → concrete method) |
| `--direct` | off | USED BY shows only direct references (skip member-level grouping) |
| `--with-imports` | off | Include PHP `use` statement references |
| `--json` / `-j` | off | Emit contract-compliant JSON |

### Picking `--depth`

- **1** — immediate neighbors. Fast, ideal for "who calls this".
- **2** — neighbors-of-neighbors. Useful for tracing one hop through a service.
- **3** — full call tree. What `enrich-flows` uses internally.
- **>3** — rarely useful; hits `--limit` first on real codebases.

### When to set `--impl`

For **interface** or **abstract method** targets, the default uses tree
stops at the interface boundary. `--impl` follows OVERRIDES to every
concrete implementor and expands their execution flow too. Essential
when the user asks "what actually runs when X is called".

## MCP — `kloc_context`

```json
{
  "symbol": "OrderService::createOrder",
  "depth": 2,
  "limit": 50,
  "include_impl": false
}
```

Returns the same tree as the CLI's `--json` output. The MCP shape is
ContextOutput — see "Output structure" below.

## How to read the output

### Top of result — `target` + `definition`

```
target:     fqn, file, line, signature
definition: structural metadata for the target's kind
  • Method     → arguments[], return_type, declared_in
  • Class      → properties[], methods[], extends, implements, uses_traits, constructor_deps
  • Interface  → methods[], extends
  • Property   → type, visibility, promoted, readonly, static
  • Value      → value_kind, type{}, source{}
```

This is the "header" — everything you need to know about the symbol
itself before looking at how it relates to others.

### `usedBy` — "who calls this?"

A tree of caller chains, BFS-expanded to `--depth`.

| Field | Meaning |
| --- | --- |
| `depth` | 1 = direct, 2 = caller's caller, … |
| `fqn` | Fully qualified name of the caller |
| `kind` | `Class` / `Method` / `Property` / `Value` |
| `file`, `line` | 1-based source location of the **reference**, not the caller's definition |
| `refType` | What sort of reference this is — see ref-type matrix below |
| `member_ref` | (method-level only) the specific member referenced inside the caller |
| `sites` | (when one caller has many references) array of site locations; `line` is omitted at the entry level |
| `via_interface` | True if the caller depends on an interface, not the concrete type |
| `children` | Recursive — depth-N expansion |

### `uses` — "what does this call?"

Mirror of `usedBy`, plus extras for method targets:

| Field | Meaning |
| --- | --- |
| `arguments` (or `args`) | Argument-to-parameter mapping at a call site. Method-level uses rich `[{position, param_name, value_expr, value_source, value_type, source_chain}]`; class-level uses flat `{param: expr}`. |
| `callee` | For `refType: "method_call"` — the called method's FQN |
| `on`, `onKind` | The receiver type at the call site (e.g. `on: "App\\Service\\OrderService"`, `onKind: "Class"`) |
| `result_var` | When the call result is assigned, the variable name (`$order` for `$order = $svc->create(...)`) |
| `implementations` | (`--impl`) Each concrete impl's own execution flow, nested under the interface call |
| `entry_type`, `variable_name`, `variable_type`, `source_call` | Variable-centric flow — when tracing how a Value got its content |
| `crossed_from` | (depth ≥ 2) The caller that brought us into this scope |

### Ref-type matrix

| `refType` | Where it shows | What it means |
| --- | --- | --- |
| `extends` | uses (class) | Class extends parent class |
| `implements` | uses (class) | Class implements interface |
| `uses_trait` | uses (class) | Class uses a trait |
| `property_type` | uses (class) | Property declares a typed class/interface |
| `parameter_type` | uses | Method param declares a typed class/interface |
| `return_type` | uses | Method declares a typed return |
| `instantiation` | uses | `new X(...)` site |
| `method_call` | uses | `$obj->method()` or `Class::method()` site |
| `property_access` | uses | `$obj->prop` read/write |
| `type_hint` | uses | `instanceof X` or `function (?X $a)` |
| `override` | usedBy / uses | Method overrides another (class-level) |
| `inherited` | uses | Method comes from a parent class via inheritance |

Entries without a `refType` are the bare call/use; entries with one are
**structural** (no execution semantics, just "this code mentions X").

### Multi-site grouping (`sites`)

When the same caller uses a class many times (e.g. `OrderService`
mentions `Order` in 8 different places), kloc-intelligence collapses
them into a single entry with `sites: [{file, line, refType}, ...]`. The
top-level `line` field is omitted because there are many. Branch on
`sites` presence in your JSON parser:

```python
if "sites" in entry:
    for site in entry["sites"]:
        ...
else:
    use entry["line"]
```

### Property-group entries

Property targets get a special grouping in USED BY: `{property, accessCount, methodCount}` — "$total is accessed 12 times across 3 methods."

## Tips

- **Start narrow, widen later** — depth=1, no --impl, look at the
  shape. Then add depth / --impl as needed.
- **Use `--json` for parsing** — the Rich-tree output is for humans;
  agents should pipe through `--json`.
- **--impl flips the meaning of "uses"** — for interfaces, default
  `uses` lists method signatures; `--impl` lists concrete behavior.
- **Methods get an execution flow; classes don't** — class `uses`
  reports structural relations (extends, prop types, method-call sites
  collapsed); method `uses` reports the call sequence.
- **`crossed_from` only appears at depth ≥ 2** — it's the breadcrumb
  back to whichever caller's frame this entry belongs to.

## Suggested workflow

1. Resolve first (`kloc_resolve`) to confirm the symbol exists and pick
   the right candidate.
2. `context` at depth=1 to see immediate neighbors.
3. If interesting, deepen to 2 or 3, optionally with `--impl`.
4. For ambiguous receiver chains, look at `arguments[].source_chain` to
   trace where a value came from.
5. For "what does this flow actually do at runtime", chain into `source`
   or `chunks` for the actual method body.
