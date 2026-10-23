# AGENTS.md

Guide for AI agents working with the RubyGemDB codebase.

---

## Project Overview

RubyGemDB is a Ruby gem analysis and classification tool that categorizes gems into architectural patterns using heuristic analysis and LLM fallback for ambiguous cases.

### Functional Categories
21 functional categories, each tagged with a stack layer (`substrate`, `plumbing`, `composition`, `quality`), defined in a single module: `src/rubygemdb/models/categories.py`. The taxonomy is oriented around the project's primary purpose — assisting back-end tooling design — so search and retrieval can present gems as a layered stack.

Every category also defines a `sub_categories` vocabulary — fine-grained facets (slug → example gems, 80 facets across all 21 categories) used for classification, search, and display. The vocabularies live only in `categories.py`; `tests/test_categories.py` enforces at least two facets per category and pins the exact slugs.

**substrate** (runtime base everything stands on):
1. `core_extensions` — Core Extensions (activesupport, facets, bigdecimal, securerandom, dotenv)
2. `native_bindings` — Native Bindings (ffi, fiddle, rake-compiler, ruby-macho)
3. `servers_concurrency` — Servers & Concurrency (puma, falcon, async, concurrent-ruby, childprocess)

**plumbing** (data in/out and processing):
4. `http_networking` — HTTP & Networking (faraday, httparty, excon, aws-sdk-s3, google-apis-drive_v3)
5. `persistence` — Persistence (activerecord, sequel, pg, sqlite3, redis, shrine)
6. `background_jobs` — Background Jobs (sidekiq, solid_queue, resque, gush, async-job)
7. `document_parsing` — Document Parsing (nokogiri, rexml, csv, pdf-reader, commonmarker, yajl-ruby)
8. `document_generation` — Document Generation (prawn, pdfkit, wicked_pdf, asciidoctor-pdf, rouge)
9. `text_search` — Text & Search (pragmatic_segmenter, amatch, elasticsearch, searchkick, bm25f)
10. `media_processing` — Media Processing (ruby-vips, wavefile, taglib-ruby, ruby-sox, aubio)

**composition** (the shape of the application being built):
11. `web_frameworks` — Web Frameworks (rails, sinatra, roda, rack, haml, turbo-rails)
12. `cli_libraries` — CLI Libraries (thor, gli, drydock, highline; sub-categories: cli_frameworks, terminal_ui, terminal_styling, terminal_output)
13. `gui_desktop` — GUI & Desktop (glimmer-dsl-libui, tk, gosu)
14. `static_site_generation` — Static Site Generation (jekyll, cvless, hacked-jekyll, asciidoctor, beckett)
15. `ai_llm` — AI & LLM (ruby_llm, ruby-openai, rllama; sub-categories: llm_clients, agent_frameworks, embeddings_vector, prompt_tooling)
16. `mcp_tooling` — MCP Tooling (ruby-mcp-client, ruby_llm-mcp)
17. `runtime_validation` — Runtime Validation (dry-schema, dry-types, activemodel, schematist, hashie)
18. `security_auth` — Security & Auth (devise, pundit, bcrypt_pbkdf, ed25519, symmetric-encryption)

**quality** (correctness and maintainability of the stack):
19. `testing_qa` — Testing & QA (rspec, capybara, factory_bot, webmock, selenium-webdriver)
20. `code_quality_typing` — Code Quality & Typing (rubocop, brakeman, sorbet, rbs, ruby-lsp)
21. `developer_tools` — Developer Tools (pry, debug, stackprof, vernier, bundler-audit)

Each category also carries curated `base_gems` stack seeds (e.g. dotenv, drydock, pry, rubocop, journald-logger) used by the `stack` command and the RAG agent to scaffold recommended gem stacks for back-end tooling.

---

## Architecture

### Data Flow
1. Read gem inventory from CSV → Fetch RubyGems API data → Heuristic classification → LLM enhancement (optional) → Save results to YAML/SQLite
2. Caching layers to minimize redundant API/LLM calls
3. Persistent storage for classified gem results

### Core Component Directory Structure
```
src/rubygemdb/
├── agent.py            # Contextual GraphRAG chat & tool-calling agent with persistent sessions
├── cli.py              # Command-line interface (process / reclassify / stack)
├── mcp_server.py       # Native MCP server & daemon lifecycle (FastMCP)
├── ui/
│   └── tui.py          # Interactive Textual TUI explorer & GraphRAG chat
├── core/
│   └── config.py       # Settings and environment variable handling
├── models/
│   ├── gem.py          # Pydantic data models for gem data
│   ├── phases.py       # Build phase taxonomy & search filters
│   ├── categories.py   # Single source of truth: 21 categories, layers, sub-category facets
│   └── session.py      # GraphRAG session models (RAGSession, RAGMessage, RAGTrace)
├── services/
│   ├── container_runner.py # Podman/Docker runner for indexer container
│   ├── gem_fetcher.py  # Git shallow-cloner with .gem unpack fallback
│   ├── graphrag.py     # Two-stage GraphRAG retrieval & prompt synthesizer
│   ├── hybrid_search.py # SQLite FTS5 + txtai vector search, RRF fusion
│   ├── query_expander.py # Local GGUF query expansion → lexical/vector variations (off by default)
│   ├── reranker.py     # Local GGUF cross-encoder reranking of candidates (off by default)
│   ├── model_manager.py # Hugging Face GGUF download + llama.cpp instance cache (data/models/)
│   ├── classifier.py   # Keyword/description/embedding classification + sub-category faceting
│   ├── embedding_scorer.py # txtai embedding similarity vs category descriptions
│   ├── prune.py        # Overlap/stale gem prune reports
│   ├── stack.py        # Layered gem-stack composition
│   ├── rubygems.py     # RubyGems API client with caching
│   ├── llm.py          # Mistral LLM client for fallback classification
│   └── context7.py     # Context7 API client; repo-anchored library-ID verification
└── storage/
    ├── base.py         # Storage interface
    ├── json_storage.py # JSON file storage implementation
    ├── sqlite_storage.py # SQLite storage implementation
    └── session_storage.py # Persistent GraphRAG session store (data/sessions.sqlite)
docker/
├── indexer.Dockerfile  # Ruby 4.0.4 runtime packaging rubydex, decode, bake
└── extract_graph.rb    # Container script extracting AST symbols & structured decode docs
```

---

## Key Commands

### Install Dependencies
```bash
# Using uv (recommended) - CPU default
uv sync --extra cpu

# With optional CUDA support (NVIDIA GPU acceleration)
uv sync --extra cuda

# Or pip (dev tooling is managed by uv's [dependency-groups]; there is no dev-requirements.txt)
pip install -e .
```

### Batch Classification (CLI)
Process a CSV of gem names and generate YAML output reports:
```bash
# Direct execution
python -m rubygemdb gems-inventory.csv --out output/

# As installed package
rubygemdb gems-inventory.csv --out output/

# With uv
uv run rubygemdb gems-inventory.csv --out output/
```

### Interactive TUI Explorer
Launch terminal UI for browsing and managing classified gems:
```bash
# Direct execution
python -m rubygemdb.ui.tui [optional-gems-inventory.csv]

# As installed package
rubygemdb-tui [optional-gems-inventory.csv]

# With uv
uv run rubygemdb-tui [optional-gems-inventory.csv]
```

### Reclassification (fresh start under the current taxonomy)
Produces an approval-gated prune report (`data/prune-report.md` listing overlapping and stale gems — no release in 3+ years), then wipes and rebuilds all classified data (SQLite `classified_gems` table, `classified_gems.json`, txtai index, per-category YAMLs) from the pruned inventory:
```bash
# Review the prune report first, then apply it
uv run rubygemdb reclassify --out output/          # interactive prune confirmation
uv run rubygemdb reclassify --apply-prune --embed  # non-interactive: full report + rebuild txtai index
uv run rubygemdb reclassify --prune-list prune.txt --embed  # prune only the gems named in the file
```
The prune list file is plain text: one gem name per line, `#` comments allowed — use it to approve a subset instead of the whole report.
A dated SQLite backup (`data/rubygemdb.sqlite.bak-<date>`) is written before anything is wiped. Run with ollama up for best embedding-assisted classification.

### Gem-Stack Composition
Turn a context query into a layered stack manifest (layer, category, gem, role) plus a Gemfile snippet, starting from the curated base picks (dotenv, drydock, pry, rubocop, journald-logger):
```bash
uv run rubygemdb stack "CLI data pipeline tool"           # print to stdout
uv run rubygemdb stack "CLI data pipeline tool" --out stacks/  # also write files
```
Output is advisory, not a dependency resolver. Deeper Rubysmith project generation from a stack manifest is a follow-up goal.

### MCP Server (`rubygemdb-mcp`)
Native Model Context Protocol server exposing catalog tools (`search_gems`, `get_gem`, `compose_stack`, `list_categories`, `build_gem_graphrag`, `query_gem_graphrag`), session tools (`list_rag_sessions`, `get_rag_session`, `resume_rag_session`, `export_rag_session_transcript`, `delete_rag_session`), resources (`rubygemdb://taxonomy`, `rubygemdb://stack-seeds`), and prompt templates (`design-ruby-stack`, `gem-architect-rag`):
```bash
# Stdio transport (default, for host agents)
uv run rubygemdb-mcp

# Background SSE daemon lifecycle (idempotent: start on a running daemon exits 0)
uv run rubygemdb-mcp --daemon start --port 8011
uv run rubygemdb-mcp --daemon status
uv run rubygemdb-mcp --daemon stop
uv run rubygemdb-mcp --sse          # shortcut for --daemon start
```
Other flags: `--transport stdio|sse` (default `stdio`), `--host` (default `127.0.0.1`), `--port` (default `8000`), `--foreground` (SSE in the foreground; used by the daemon worker), `--pid-file` (default `data/mcp.pid`), `--log-file` (default `data/mcp.log`). Daemon artifacts: `data/mcp.pid`, `data/mcp.json` (host/port sidecar), `data/mcp.log`.

`search_gems` filters: `category` — one of the 21 slugs; `phase` — `skeleton`/`input`/`processing`/`output`. FTS5 first; the txtai vector index loads lazily and only when FTS returns zero hits. Catalog tools open the gem database read-only (`mode=ro`); mutating storage methods raise `PermissionError` in that mode. Session tools write only to the separate `data/sessions.sqlite` store — the gem catalog is never modified. `build_gem_graphrag` and `query_gem_graphrag` accept an optional `session_id` (auto-generated when omitted) and return the `session_id` they recorded the turn in; `resume_rag_session` pre-warms the session's gem index and returns the last 5 messages (full context restore happens in the TUI).

### Linting & Type Checking
```bash
# Ruff for linting
uv run ruff check src/rubygemdb/

# Mypy for type checking
uv run mypy src/rubygemdb/
```

---

## Environment Variables

Required for full functionality:
```bash
# Mistral API key for LLM fallback classification
export MISTRAL_API_KEY="your_mistral_key"

# Context7 API key for generating gem usage cheatsheets
export CONTEXT7_API_KEY="your_context7_key"
```

Optional retrieval-quality stages (both off by default; GGUF models download lazily from Hugging Face into `data/models/` on first use):
```bash
export QUERY_EXPANSION_ENABLED="true"  # expand queries into lexical/vector variations (mradermacher/qmd-query-expansion-1.7B-GGUF)
export RERANKER_ENABLED="true"         # cross-encoder rerank of the candidate pool (ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF)
```
Further knobs: `QUERY_EXPANDER_REPO_ID` / `QUERY_EXPANDER_FILENAME` / `QUERY_EXPANDER_N_CTX` / `QUERY_EXPANDER_N_THREADS`, `RERANKER_REPO_ID` / `RERANKER_FILENAME` / `RERANKER_N_CTX` / `RERANKER_N_THREADS`, `RERANKER_CANDIDATE_MULTIPLIER` (default 3; candidate pool = top_k × multiplier, clamped 15–50), and `MODELS_CACHE_DIR` (default `data/models/`). Legacy aliases `EXPANDER_*`, `CROSS_ENCODER_*`, `CANDIDATE_MULTIPLIER`, and `MODEL_CACHE_DIR` are also accepted. Both stages degrade to baseline retrieval when disabled, missing, or failing — no exceptions surface to callers.

---

## Classification Logic


### Dynamic RAG Workflow
1. User enters a query (optionally selecting a target codebase project from the TUI dropdown).
2. The query is processed by the "Other Steve" Prompt Architect to generate strict, SFL-compliant semantic variations.
3. The variations are searched against the `txtai` database (documents carry `category` and `layer` metadata), applying a 30% recency boost for updated gems. When `QUERY_EXPANSION_ENABLED` is on, the local GGUF `QueryExpander` adds lexical/vector variations of its own (multi-pass, RRF-fused over an oversampled candidate pool); when `RERANKER_ENABLED` is on, the fused candidates are re-scored by the local cross-encoder before truncation. Both stages are off by default and fall back to baseline retrieval when their models are unavailable.
4. For back-end tooling queries, results are presented grouped by layer (substrate first, quality last), and recommendations start from the category `base_gems` seeds before context-specific gems.
5. If a target project is selected, the agent calls `get_architecture` and `search_graph` via Codebase Memory MCP to inspect the target structure.
6. The agent synthesizes the Context7 cheatsheets, semantic gem matches, and actual codebase structure to output a strict Pragmatic Implementation Backlog.
7. Chat sessions persist across restarts: every turn is saved to `data/sessions.sqlite` and indexed into txtai as a `session_turn` document tagged with its session id; the TUI restores the most recent session (active gems, intention, chat history, last 200 execution traces) on open.

### Classification Pipeline
Classification combines three scoring signals; the best-scoring category wins:
- Keyword rules: gem name (token match) and dependency substrings, re-keyed to the new slugs via `categories.py`
- Description-text scoring: RubyGems `info` term hits per category keywords/description
- txtai embedding similarity (`EmbeddingScorer`, model `ollama/embeddinggemma`) between `"{name}: {description}"` and category description vectors; degrades gracefully to keyword + description scoring when ollama is down

Name-keyword matches score 0.8 confidence; dependency-only 0.65; description/embedding-only ≤ 0.6; unknown gems default to `core_extensions` at 0.4.

### Sub-Category Detection
After the primary category is chosen, `GemClassifier.detect_sub_categories` scores that category's `sub_categories` vocabulary against the gem:
- Example-gem name match +10.0; example-gem substring in the name +3.0
- Sub-slug token hits: +2.0 per name hit, +0.5 per description hit
- Example gems in the dependency list: +2.5 each
Facets scoring ≥ 2.0 are kept, best-first; if none clears the bar, the best description-token sub-slug is used as a fallback. Detection re-runs after an LLM category override, so facets always match the final primary category.

### LLM Fallback
The Mistral LLM API is always consulted to generate the agent description; the heuristic-confidence gate (< 0.7) only decides whether the LLM's category (itself requiring > 0.6 confidence) may override the heuristic pick.

---

## Caching Strategy

Local cache files:
1. `data/cache/gem_cache.json`: Raw RubyGems API responses
2. `data/cache/llm_cache.json`: SHA256-hashed LLM prompt/response pairs
3. `classified_gems.json`: Persisted TUI classification results
4. `data/rubygemdb.sqlite`: Central SQLite database for inventory and classified gems (the `classified_gems` table carries a `sub_categories` column, auto-added by migration on existing databases)
5. `data/txtai/`: Layer-aware txtai embedding index
6. `data/cache/category_vectors/`: Cached category-description vectors for embedding scoring
7. `data/prune-report.md`: Approval-gated overlap/stale gem report
8. `data/mcp.pid` + `data/mcp.json` + `data/mcp.log`: MCP daemon PID file, host/port sidecar, and daemon log
9. `data/sessions.sqlite`: Persistent GraphRAG session store (`rag_sessions`, `rag_messages`, `rag_traces` tables, auto-created on first use via `SessionStorage`; separate from the gem catalog, which stays read-only)
10. `data/models/`: Local GGUF model cache for query expansion and reranking (lazily downloaded from Hugging Face via `services/model_manager.py`; the directory is auto-created at startup)

---

## Development Workflow

1. Install dependencies with `uv sync --dev`
2. Make changes to code in `src/rubygemdb/`
3. Test with local execution: `uv run rubygemdb data/gems-inventory.csv --out test-output/`
4. Lint and type check: `uv run ruff check && uv run mypy`

---

## Data Formats

### Input CSV
Requires either `name` or `gem` column for gem names. Optional columns:
- `group`: Development/test context
- `category`: Initial category hint
- `description`: Gem description
- `homepage`: Gem homepage URL
- `context7_id`: Context7 library ID for cheatsheet generation. IDs are canonicalized to `/owner/repo` form when a GitHub/GitLab/Codeberg repo URL can be derived from the gem's source or homepage URI (`services/context7.py`), so manually curated IDs should follow that form

### Output YAML
Per-category YAML files contain structured gem classification data:
```yaml
- name: gem_name
  classification:
    primary: category_name  # one of the 21 slugs
    confidence: 0.85
    sub_categories: [terminal_styling]  # facet slugs from the category's vocabulary
  role:
    description: "Gem metadata description"
    attaches_to: "category_prefix"
  risks:
    invasiveness: 3
    coupling: 2
    abstraction_leak: "low"
  signals:
    rails: true
    external_io: false
    native_ext: false
  dependencies: ["activesupport"]
```

---

## Gotchas & Non-Obvious Patterns

1. **Confidence Thresholds**: Heuristic results <0.7 trigger LLM processing; LLM results must have >0.6 confidence to override heuristics
2. **Cache Persistence**: Cache files are automatically saved after successful API/LLM calls
3. **Rate Limiting**: Built-in delays between API requests: 0.5s between RubyGems calls, 0.5s between LLM calls
4. **Batch Size**: Default LLM batch size is 10 for efficient bulk processing
5. **TUI Requirements**: CSV input only required for first run to populate SQLite database

---

## Testing

The litmus pytest suite verifies taxonomy integrity, sub-category vocabulary coverage (every category has ≥ 2 facets, exact slugs pinned), classification expectations (jekyll→static_site_generation, nokogiri→document_parsing, thor→cli_libraries, sidekiq→background_jobs, ruby_llm→ai_llm), migration completeness (no old slugs under `src/`), and stack composition:
1. Run the suite: `uv run pytest tests/`
2. Validate classification logic with sample gems: `uv run python -m rubygemdb data/sample-gems.csv --out test-output/`

## Existing Documentation

Additional context available in:
- `CLAUDE.md`: Claude-specific guidance
- `GEMINI.md`: Project documentation (kept current with AGENTS.md)