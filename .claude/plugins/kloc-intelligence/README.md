# kloc-intelligence — Claude Code plugin

Project-local Claude Code plugin that bundles the kloc-intelligence MCP
server plus six task-oriented skills.

When this repo is opened in Claude Code:

- The MCP server `kloc-intelligence` auto-starts via
  `uv --directory <repo> run kloc-intelligence mcp-server`, exposing all
  16 MCP tools (`kloc_resolve`, `kloc_context`, `kloc_flows`,
  `kloc_search`, …).
- Six skills become available under the `kloc-intelligence:` namespace:
  - `kloc-intelligence:setup` — env + Docker + schema bring-up
  - `kloc-intelligence:ingestion` — sot.json + flows + enrichment pipeline
  - `kloc-intelligence:flows` — Symfony flow surface (list / detail / search)
  - `kloc-intelligence:context` — bidirectional traversal + how to read the output
  - `kloc-intelligence:search` — semantic search across the 3 collections
  - `kloc-intelligence:chunks` — source + chunked source

Skills auto-trigger when their `description` matches the user's request,
or you can call them explicitly with `/kloc-intelligence:<skill-name>`.

## Layout

```
.claude/plugins/kloc-intelligence/
├── .claude-plugin/plugin.json    # manifest
├── .mcp.json                     # MCP server config
├── README.md
└── skills/
    ├── setup/SKILL.md
    ├── ingestion/SKILL.md
    ├── flows/SKILL.md
    ├── context/SKILL.md
    ├── search/SKILL.md
    └── chunks/SKILL.md
```

## Activation

No action needed. Claude Code auto-discovers plugins under
`.claude/plugins/` at session start.

To check the server is up: `/mcp` should list `kloc-intelligence` with 16
tools.
