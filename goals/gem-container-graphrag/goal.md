# Goal: Ephemeral Gem GraphRAG Pipeline & Advisor Retirement

## Articulated Goal

Retire the card-and-phase-walking Advisor service from RubyGemDB's TUI and backend agent, replacing it with an on-demand, containerized GraphRAG pipeline. When provided with a project intention and selected gems, the pipeline clones each gem repository into a host cache, spins up a temporary container packaging `rubydex` and `decode` to extract AST symbol hierarchies and structured documentation, builds a contextual GraphRAG (semantic vectors + graph expansion) on the host, and grounds interactive agent chat in the TUI as well as MCP calls.

## Shared Understanding

See [facts.md](facts.md) — 20 accepted facts covering Advisor removal, container definition & runner, gem cloning & fallback, AST & documentation extraction with `rubydex` and `decode`, host GraphRAG ingestion & vector search, TUI Chat tab redesign, MCP server tools, and test suite verification.

## Execution Plan

See [plan.md](plan.md) — 9 ordered steps:
1. Retire Advisor service and clean phase model
2. Container definition and Ruby graph extraction script
3. Gem source resolution, caching, and fallback fetcher
4. Python container runner service
5. Graph ingestion, ephemeral txtai vector store, and GraphRAG engine
6. Agent refactoring and grounded chat integration
7. TUI Chat tab transformation
8. MCP server tools and prompts
9. Linting, type checking, and documentation updates

## Done Condition

All 20 facts hold on the real codebase:
- `uv run ruff check src/rubygemdb/`, `uv run mypy src/rubygemdb/`, and `uv run pytest tests/` pass cleanly.
- No `AdvisorSession`, `advisor.py`, `advisor_export.py`, or `test_advisor.py` files or references remain in `src/rubygemdb/`.
- A temporary container image (`rubygemdb-indexer:latest`) successfully extracts AST symbols via `rubydex` and structured documentation via `decode` from sample gem repositories.
- The TUI Chat tab presents the Contextual GraphRAG interface, syncs selected gems, streams container progress, and executes grounded chat queries.
- `src/rubygemdb/mcp_server.py` exposes `query_gem_graphrag`, `build_gem_graphrag`, and the `gem-architect-rag` prompt template.
