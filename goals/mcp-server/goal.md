# Goal: Model Context Protocol (MCP) Server for RubyGemDB

## Articulated Goal

Implement native Model Context Protocol (MCP) server support for RubyGemDB so CLI coding agents (Claude Code, Antigravity, OpenCode, Cursor) can directly discover Ruby gems, query classifications and architectural layers, and compose layered gem stacks. Expose an executable `rubygemdb-mcp` CLI entrypoint running over stdio by default for host agent child-process spawning, alongside `--daemon start|status|stop` lifecycle commands to run detached in the background over SSE. All MCP tools operate strictly in read-only mode with instant sub-millisecond FTS5 search readiness, deferring heavy vector embedding loads.

## Shared Understanding

See [facts.md](facts.md) — 13 accepted facts covering the entrypoint, stdio default transport, SSE daemon lifecycle management, read-only tools (`search_gems`, `get_gem`, `compose_stack`, `list_categories`), static resources (`rubygemdb://taxonomy`, `rubygemdb://stack-seeds`), prompt template (`design-ruby-stack`), fast startup performance, and automated test suite.

## Execution Plan

See [plan.md](plan.md) — 6 ordered steps: dependencies & entrypoint registration → FastMCP server core, tools, resources & prompts → CLI & daemon lifecycle management → automated test suite → linting & type checking → documentation & agent configuration.

## Done Condition

All 13 facts hold on the real codebase:

- `uv run ruff check src/rubygemdb/ tests/` and `uv run mypy src/rubygemdb/` pass cleanly.
- `uv run pytest tests/test_mcp.py` and `uv run pytest tests/` pass (100% green).
- `rubygemdb-mcp` launches as a stdio MCP server when invoked without arguments.
- `rubygemdb-mcp --daemon start --port 8011`, `rubygemdb-mcp --daemon status`, and `rubygemdb-mcp --daemon stop` start, monitor, and stop the background SSE daemon process reliably.
- All MCP tools (`search_gems`, `get_gem`, `compose_stack`, `list_categories`), resources, and prompts respond accurately and operate in read-only mode without mutating the SQLite database.
