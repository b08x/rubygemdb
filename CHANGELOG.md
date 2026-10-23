## [Unreleased]

### 🚀 Features

- Add optional Langfuse observability and tracing (`services/tracing.py`): context-manager spans over gem classification, LLM calls (with token usage), hybrid search, GraphRAG retrieval stages, agent chat turns, and MCP GraphRAG tools
- Add settings `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (alias `LANGFUSE_BASE_URL`), and `LANGFUSE_ENABLED`; the host defaults to `https://cloud.langfuse.com`
- Tracing is disabled unless both keys are set; `LANGFUSE_ENABLED=false` disables it even with keys present. When disabled, traced blocks yield no-op observations and the Langfuse client is never initialized (the SDK imports lazily, only when enabled)
- Trace names are kebab-case (`classify-gem`, `classify-gem-llm`, `rag-chat-turn`, `graphrag-retrieve`, `mcp-build-gem-graphrag`, …) and tagged `rubygemdb`; GraphRAG chat turns group traces by session id
- Exceptions raised inside a traced operation are recorded with `level=ERROR` and a status message before being re-raised
- The CLI flushes buffered traces via `flush_tracing()` when `process`, `reclassify`, `stack`, or `build-indexer` finish, on success or failure
- Add `langfuse>=5.0.4` runtime dependency (pulls in the OpenTelemetry SDK family)

### 🧪 Testing

- Add test suite `tests/test_tracing.py` covering the no-op fallback contract, span/generation creation, error capture, session-id propagation, and flush behavior

### 📚 Documentation

- Document Langfuse configuration and tracing behavior in README.md, CLAUDE.md, and INSTALL.md (AGENTS.md and GEMINI.md were updated alongside the code in the same commit)

## [0.8.0] - 2026-10-23

### 🚀 Features

- Add optional local query expansion and cross-encoder reranking to both retrieval pipelines (hybrid gem search and GraphRAG) — off by default, enabled via `QUERY_EXPANSION_ENABLED` / `RERANKER_ENABLED`
- Add `services/query_expander.py`: expands a query into lexical and vector variations using `mradermacher/qmd-query-expansion-1.7B-GGUF` (Qwen3 1.7B, `/no_think` prompt template) via `llama_cpp`
- Add `services/reranker.py`: cross-encoder reranking using `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF`, scoring each query-passage pair as the logit probability of the "yes" token
- Add `services/model_manager.py`: lazy Hugging Face Hub download and in-memory `llama_cpp` instance caching of GGUF models under `data/models/`
- Run multi-pass retrieval over the original query plus its expansions and fuse all passes with Reciprocal Rank Fusion; oversample the candidate pool to `top_k × RERANKER_CANDIDATE_MULTIPLIER` (default 3, clamped 15–50) before reranking truncates to the requested limit
- Degrade transparently to baseline unexpanded/un-reranked retrieval when the models are disabled, not yet downloaded, or fail at runtime — no exceptions surface to callers
- Attach a `rerank_score` to results when reranking is active

### 🧪 Testing

- Add test suite `tests/test_query_expansion_reranking.py` covering settings defaults and legacy env aliases, model-manager download/caching/fallback, expansion prompt and parsing, reranker prompt and scoring, hybrid-search multi-pass and pool sizing, and GraphRAG expansion/rerank fallbacks

### 📚 Documentation

- Document query expansion and reranking configuration, the `data/models/` GGUF cache, and the new `huggingface_hub` / `llama-cpp-python` dependencies in README.md, AGENTS.md, CLAUDE.md, GEMINI.md, and INSTALL.md
- Correct pre-existing factual errors: rate-limit delays (0.5s, not 0.1s/0.2s), the cheatsheets output path in CLAUDE.md, and unqualified `llm_cache.json` paths

## [0.7.0] - 2026-10-23

### 🚀 Features

- Add persistent GraphRAG session tracking across the TUI, agent, and MCP server — sessions, chat messages, and execution traces survive restarts
- Add dedicated session store `data/sessions.sqlite` (`rag_sessions`, `rag_messages`, `rag_traces` tables, auto-created on first use), keeping the gem catalog database strictly read-only
- Add session models `RAGSession`, `RAGMessage`, and `RAGTrace` (`models/session.py`) and the `SessionStorage` SQLite backend (`storage/session_storage.py`)
- Expose five session MCP tools: `list_rag_sessions`, `get_rag_session`, `resume_rag_session`, `export_rag_session_transcript`, and `delete_rag_session`
- Add optional `session_id` parameter to `build_gem_graphrag` and `query_gem_graphrag`; both persist turns to the session store and return the `session_id`
- Add TUI session picker with New Session / Delete Session buttons; the chat tab auto-restores the most recent session (active gems, intention, history, last 200 traces) on open
- Index every chat turn into txtai embedding memory as a `session_turn` document tagged with its session id
- Export session transcripts in markdown and JSON from the persistent session store

### 🧪 Testing

- Add test suite `tests/test_session_tracking.py` covering session storage CRUD, transcript export, agent session binding, MCP session tools, and TUI session restore

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
