## [Unreleased]

## [0.9.0] - 2026-10-25

### 🚀 Features

- Add refined gem stack selection (`services/stack.py`): two-stage hierarchical retrieval and second-pass curation pipeline. Replaces unconditional seed dumps with category relevance gating based on semantic search hits and lexical query tokens; base seeds and user-preferred gems only enter the candidate pool if their category is relevant to the query.
- Add second-pass curation engine (`services/llm.py`, `services/stack.py`): curates candidate pool down to a compact stack of 5–12 gems with category density limits (max 1–3 gems per category) and assigns contextual, task-specific architectural role descriptions via LLM synthesis when available or local scoring offline.
- Add `--max-gems` parameter (default 10) to `rubygemdb stack` CLI and MCP `compose_stack` tool to govern stack density.
- Add interactive CLI multi-select gem pruning (`rubygemdb prune`, `ui/selector.py`): dual-engine interactive selector (`gum choose --no-limit` with native Textual `SelectionList` fallback) supporting candidate lists from automated audits or markdown reports.
- Add GitHub starred gems importer (`rubygemdb import-stars`, `services/github_stars.py`, `services/import_stars_pipeline.py`): fetch starred Ruby repos, resolve to published gems via API or remote gemspec, enrich inventory metadata, infer GenAI tooling roles, and execute approval-gated prune audits.
- Add optional Langfuse observability and tracing (`services/tracing.py`): context-manager spans over gem classification, LLM calls (with token usage), hybrid search, GraphRAG retrieval stages, agent chat turns, and MCP GraphRAG tools
- Add settings `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (alias `LANGFUSE_BASE_URL`), and `LANGFUSE_ENABLED`; the host defaults to `https://cloud.langfuse.com`
- Tracing is disabled unless both keys are set; `LANGFUSE_ENABLED=false` disables it even with keys present. When disabled, traced blocks yield no-op observations and the Langfuse client is never initialized (the SDK imports lazily, only when enabled)
- Trace names are kebab-case (`classify-gem`, `classify-gem-llm`, `rag-chat-turn`, `graphrag-retrieve`, `mcp-build-gem-graphrag`, …) and tagged `rubygemdb`; GraphRAG chat turns group traces by session id
- Exceptions raised inside a traced operation are recorded with `level=ERROR` and a status message before being re-raised
- The CLI flushes buffered traces via `flush_tracing()` when `process`, `reclassify`, `stack`, or `build-indexer` finish, on success or failure
- Add `langfuse>=5.0.4` runtime dependency (pulls in the OpenTelemetry SDK family)
- Add universal embedding cache (`services/embedding_cache.py`): a content-addressable SQLite vector cache at `data/cache/embeddings_cache.sqlite`, keyed by SHA-256 of model path + text, shared across GraphRAG indexing, classifier embedding scoring, and agent sessions via `wrap_embeddings()` wrapping txtai `Embeddings` instances
- Repeat vectorization of previously-seen texts skips the embedding model entirely — only missed texts are encoded and written through, in the original order; GraphRAG vectorization progress messages now report cache hits/misses per chunk
- Add settings field `embeddings_cache_file` (default `data/cache/embeddings_cache.sqlite`)
- Add embedding cache lifecycle and retention: a 180-day TTL purge and a 100,000-vector capacity trim (oldest first) via `prune()`, opportunistic inline pruning after every batch write when the capacity cap is exceeded, and physical disk reclamation via SQLite `VACUUM` — run automatically when a prune deletes more than 10% of entries. TTL is enforced at prune time, not on read — expired-but-unpruned entries still serve as cache hits — and re-putting a vector refreshes its TTL clock
- Add `rubygemdb cache {stats, clear, prune}` CLI group: `stats` renders a Rich table of vector counts, models, and disk footprint (db/WAL/total bytes); `clear` wipes all or `--model`-scoped vectors (vacuum by default, `--no-vacuum` to skip); `prune` applies TTL/capacity limits with `--max-age-days` / `--max-entries` overrides and a prune-summary table
- Add `reclassify --clear-embeddings-cache` to wipe and vacuum the embedding cache during fresh-start reclassification
- Add retention settings `embeddings_cache_max_entries` (default 100,000) and `embeddings_cache_ttl_days` (default 180); wire `embeddings_cache_file` into `EmbeddingCache`'s default path (the setting was previously declarative only)
- Add structured GraphRAG citations (`services/graphrag.py`): every rendered method, class, module, and code example in the grounded context carries a sequential `[C1]`-style citation tag with its source file, plus an appended `### Grounded Citations Index` table; citation records (`id`, `gem`, `file`, `node_id`, `node_type`, `symbol`, `signature`, `summary`) ride along on the retrieval result
- Add an anti-hallucination prompt contract for GraphRAG-grounded agent turns (`agent.py`): the model may only use APIs that appear in the citations, unsupported capabilities are stated explicitly (existing methods cited, missing logic supplied as clearly-distinguished custom Ruby code), replies carry inline `[Cn]` tags, and each answer closes with a `### Citations` summary section — the contract binds grounded turns; turns without an indexed gem context (no gems selected) run as ordinary ungrounded chat rather than refusing
- Add a `citations` column to `rag_messages` (auto-migrated via `ALTER TABLE` on existing `data/sessions.sqlite` stores — no recreation needed) and a `citations` field to `RAGMessage`; agent and MCP turns persist their citations, and unparseable stored citations degrade to an empty list on load
- `query_gem_graphrag` MCP results gain a top-level `citations` list; markdown transcript exports render a `> **Grounded Citations:**` block per cited message (JSON exports carry `citations` per message), replacing the raw retrieved-node line on cited turns
- Add setting `agent_max_tokens` (env `AGENT_MAX_TOKENS`, default 4096) capping agent model output — agent responses were previously uncapped

### 🐛 Bug Fixes

- Fix GraphRAG "ghost cache" loading: index cache validity now requires the saved txtai `embeddings` file, not merely an existing `txtai/` directory — a partially-written cache (graph.json present, vector index missing) previously reported a successful cached load while serving an empty vector index (searches returned nothing); the same state now triggers a full rebuild
- Fix `@returns` tag descriptions in the grounded GraphRAG context: they previously rendered only when a parameter description also existed (a copy-paste guard bug in `services/graphrag.py`), so documented return values could be silently dropped from retrieval output

### 🧪 Testing

- Add test suite `tests/test_tracing.py` covering the no-op fallback contract, span/generation creation, error capture, session-id propagation, and flush behavior
- Add test suite `tests/test_embedding_cache.py` covering cache-key hashing, single/batch put/get roundtrips, stats and clear, the wrap lifecycle (an all-hit turn performs zero calls to the underlying model), and idempotent double-wrapping
- Add `test_build_or_load_index_ghost_cache_rebuilds` to `tests/test_graphrag.py`: a ghost cache state (graph.json and `txtai/` present, `embeddings` file missing) must trigger a rebuild
- Add test suite `tests/test_cli.py` covering `cache stats` / `cache clear` (with `--model` and `--no-vacuum`) / `cache prune` output and `reclassify --clear-embeddings-cache`
- Extend `tests/test_embedding_cache.py` with retention coverage: TTL pruning of backdated rows, oldest-first capacity trimming, opportunistic inline pruning during `put_many`, the settings-driven db path, `stats()` disk-size fields, and the >10% auto-vacuum threshold
- Extend the GraphRAG test suites: `tests/test_graphrag.py` asserts sequential `[Cn]` citation tags in the rendered context and the Grounded Citations Index table; `tests/test_agent_graphrag.py` pins the anti-hallucination prompt contract; `tests/test_mcp_graphrag.py` asserts the `citations` key in `query_gem_graphrag` results; `tests/test_session_tracking.py` covers citation persistence in the session store

### 📚 Documentation

- Document Langfuse configuration and tracing behavior in README.md, CLAUDE.md, and INSTALL.md (AGENTS.md and GEMINI.md were updated alongside the code in the same commit)
- Document the universal embedding cache and the GraphRAG cache-validity fix in README.md, AGENTS.md, CLAUDE.md, GEMINI.md, and INSTALL.md
- Document embedding cache retention, the `rubygemdb cache` CLI group, and `--clear-embeddings-cache` in AGENTS.md, CLAUDE.md, GEMINI.md, and README.md (updated alongside the code); correct the stale cache description in INSTALL.md and record the feature in CHANGELOG.md
- Document GraphRAG structured citations, the anti-hallucination prompt contract, the `AGENT_MAX_TOKENS` setting, and the `rag_messages.citations` column in README.md, AGENTS.md, CLAUDE.md, GEMINI.md, and INSTALL.md; note the `citations` return of `query_gem_graphrag` in the MCP tool docstring
- Document refined stack selection (`--max-gems`), the interactive `rubygemdb prune` command, and the `rubygemdb import-stars` importer in README.md, CLAUDE.md, and INSTALL.md (AGENTS.md and GEMINI.md were updated alongside the code); refresh the stale `prune.py`/`stack.py` module-tree descriptions and add the GitHub API node to the README External Integrations diagram

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
