## [0.6.0] - 2026-10-23

### 🚀 Features

- Add native Model Context Protocol (MCP) server support with `rubygemdb-mcp` executable CLI entrypoint
- Support standard input/output (stdio) transport by default for host agent child-process spawning
- Add background SSE daemon lifecycle management (`--daemon start|status|stop`, `--sse`, `--foreground`)
- Expose read-only MCP tools: `search_gems` (instant FTS5 + lazy vector fallback), `get_gem`, `compose_stack`, and `list_categories`
- Expose static MCP resources: `rubygemdb://taxonomy` and `rubygemdb://stack-seeds`
- Expose MCP prompt template: `design-ruby-stack` for structured layered architecture design
- Enforce strictly read-only SQLite storage connections and guards across MCP operations

### 📚 Documentation

- Document MCP server usage, CLI flags, daemon artifacts, and read-only storage contract in README.md, GEMINI.md, and AGENTS.md
- Add ready-to-copy MCP configuration snippets for Claude Code, Antigravity, Cursor, and SSE clients

### 🧪 Testing

- Add automated test suite in `tests/test_mcp.py` covering tool calls, resources, prompts, and daemon lifecycle routines

## [0.5.0] - 2026-10-23

### 🚀 Features

- Initial commit - RubyGemDB gem classification tool
- Migrate from JSON to SQLite storage with Context7 integration
- Add source code URI support and enhance TUI with gem details and cheatsheets
- Add Context7 library lookup with selection modal and enhanced TUI
- *(ui)* Add manual Context7 ID entry and improve API integration
- Add sub-category classification and TUI bulk operations
- *(tui)* Add debug tab, improve navigation and error handling
- *(tui)* Add export preview and improve error handling
- Add TUI gem management with add/edit/delete operations
- Add interactive verification and RAG agent descriptions
- Add RAG agent with txtai embeddings and TUI chat screen
- Add Gemini rubygems-consult skill for Gemfile assembly
- Add parent/child chunking to txtai RAG index for finer retrieval
- Convert agent chat from modal screen to persistent tab with export
- Integrate native smolagents, query expansion, and UI context handoff
- Add recency-boosted hybrid scoring to RAG search
- Integrate Codebase Memory MCP and Dynamic RAG
- Enhance agent chat UI with distill feature and trace column
- Implement multi-agent trackboi distillation and folder selection
- Regroup taxonomy into 21 layered categories with prune/stack tooling
- Add card-driven advisor chat with hybrid RRF search
- Add preferred gem flag with stack seeding
- Add incremental find-as-you-type gem search
- Implement sub-category taxonomy and Context7 repo resolution

### 🐛 Bug Fixes

- Update Context7 API integration and improve TUI selection handling
- *(ui)* Improve export tab visibility and synchronization
- Correct mypy type annotations and missing imports

### 💼 Other

- Initial commit

Signed-off-by: Robert Pannick <rwpannick@gmail.com>
- ️ refactor: remove Trackboi distiller integration

### 🚜 Refactor

- Remove Python CLI/TUI implementation
- *(classifier)* Implement 12-category architectural classification system

### 📚 Documentation

- Add AGENTS.md guide and refine existing project documentation
- Add comprehensive README with system architecture and usage
- Add reference repo links to README
- Add architecture models and update documentation
- Add zread wiki architecture and feature documentation
- Document taxonomy-driven sub-category faceting and repo-anchored Context7 IDs

### ⚙️ Miscellaneous Tasks

- Remove stale gem cache and inventory CSV
- Add cbignore and graphifyignore, update gitignore
- Update graphify ignores and txtai index configuration
- Pruning stale docs
- Extend gitignore with local artifacts and scratch dirs
- Add pytorch cpu dependencies via uv index pinning
