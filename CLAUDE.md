# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RubyGemDB is a Ruby gem analysis and classification tool that categorizes gems into architectural patterns using heuristics and LLM-based analysis. The system provides both command-line and interactive terminal UI (TUI) interfaces for exploring and understanding Ruby gem ecosystems.

## Architecture

The system is three layers — a Pydantic/config core, services that classify and retrieve, and SQLite/JSON storage — wired as follows:

### Core Classification System
- **21 Functional Categories** (defined in `src/rubygemdb/models/categories.py`, each with a stack layer): web_frameworks, http_networking, servers_concurrency, background_jobs, persistence, document_parsing, document_generation, static_site_generation, cli_libraries, testing_qa, code_quality_typing, runtime_validation, developer_tools, ai_llm, mcp_tooling, text_search, security_auth, media_processing, gui_desktop, native_bindings, core_extensions
- **Stack Layers**: every category is tagged substrate / plumbing / composition / quality so results can be presented as a layered stack for back-end tooling design
- **Scoring Pipeline**: keyword rules (name + dependencies) + RubyGems description-text scoring + txtai embedding similarity against category descriptions; best score wins. After the primary category is chosen, a taxonomy-driven pass (`GemClassifier.detect_sub_categories`) matches the category's `sub_categories` vocabulary against the gem's name, description, and dependencies
- **LLM Refinement**: the configured LLM (default openrouter/upstage/solar-mini4, config.py:18-24) is always consulted for the agent description; heuristic confidence < 0.7 is what lets its category (confidence > 0.6) override the heuristic pick
- **Risk Assessment**: invasiveness, coupling, and abstraction-leak scoring (confidence thresholds below)

### Data Flow
1. CSV input → RubyGems API → Heuristic classification → LLM enhancement → SQLite/YAML output
2. Caching layers for both API responses (`data/cache/gem_cache.json`) and LLM responses (`data/cache/llm_cache.json`)
3. Results cached in `data/classified_gems.json` and SQLite database for TUI persistence

### Key Components
```
src/rubygemdb/
├── cli.py              # Command-line interface entry point
├── agent.py            # Contextual GraphRAG chat & tool-calling agent with persistent sessions
├── mcp_server.py       # Native MCP server & daemon lifecycle (FastMCP)
├── ui/tui.py          # Interactive Textual-based TUI with gem explorer + GraphRAG chat
├── ui/selector.py     # Dual-engine CLI multi-select (gum choose --no-limit or Textual SelectionList)
├── core/config.py     # Settings and environment variable handling  
├── models/gem.py      # Pydantic data models for gem data
├── models/phases.py   # skeleton/input/processing/output phase mapping (search filters)
├── models/session.py  # GraphRAG session models (RAGSession, RAGMessage, RAGTrace)
├── services/          # Core business logic
│   ├── container_runner.py # Podman/Docker runner for indexer container
│   ├── gem_fetcher.py  # Git shallow-cloner with .gem unpack fallback
│   ├── graphrag.py     # Two-stage GraphRAG retrieval & prompt synthesizer
│   ├── hybrid_search.py # SQLite FTS5 + txtai vector search, RRF fusion (explicit `category` filter; `phase` narrows via category keywords)
│   ├── query_expander.py # Local GGUF query expansion → lexical/vector variations (off by default)
│   ├── reranker.py     # Local GGUF cross-encoder reranking of candidates (off by default)
│   ├── model_manager.py # Hugging Face GGUF download + llama.cpp instance cache (data/models/)
│   ├── classifier.py  # Heuristic and LLM classification logic
│   ├── embedding_scorer.py # txtai embedding similarity vs category descriptions
│   ├── embedding_cache.py # SQLite-backed universal embedding cache (wrap_embeddings)
│   ├── stack.py       # Two-stage stack retrieval + second-pass curation (read_only mode for MCP)
│   ├── prune.py       # Stale/overlap prune reports + interactive multi-select backend
│   ├── github_stars.py # GitHub starred-repo fetch & gem resolution (import-stars)
│   ├── import_stars_pipeline.py # Starred-gem import, enrichment & prune audit pipeline
│   ├── rubygems.py    # RubyGems API client with caching
│   ├── llm.py         # Provider-configured LLM client for fallback classification
│   ├── context7.py    # Context7 API client for cheatsheet generation
│   └── tracing.py     # Langfuse v5 observability context managers (optional; no-op fallback)
└── storage/           # Data persistence layer
    ├── base.py        # Storage interface
    ├── json_storage.py# JSON file storage implementation
    ├── sqlite_storage.py # SQLite storage implementation (primary)
    └── session_storage.py # Persistent GraphRAG session store (data/sessions.sqlite)
```

### Contextual GraphRAG (TUI chat tab)
The chat tab provides on-demand, containerized GraphRAG for grounded Ruby gem architectural inquiry:
1. Active gems sync dynamically from selections in the Explorer tab, displayed as badges in the Chat tab
2. User enters an optional project intention and clicks `Build GraphRAG Context`
3. Ephemeral container runner (`rubygemdb-indexer:latest`, Podman priority with Docker fallback) shallow-clones repositories (with `.gem` archive unpack fallback) and runs `rubydex` (AST symbols, hierarchy, calls) and `decode` (structured docstrings, `@parameter`, `@returns`, and `@example` usage blocks), outputting `graph.json`
4. Host Python ingests `graph.json` into an ephemeral `txtai` index, cached by a deterministic SHA-256 hash of the selected gem set — a cache hit requires both `graph.json` and the saved txtai `embeddings` file; a partially-written cache (directory present, embeddings missing) triggers a rebuild instead of loading an empty index
5. Queries execute two-stage retrieval: semantic vector search for top-K nodes followed by 1-hop graph neighborhood expansion across inheritance, calls, parameters, and code examples — with query expansion enabled, vector search covers the original query plus generated variations, and with the reranker enabled, candidates are re-scored by the local cross-encoder first (both off by default, degrading to baseline retrieval when models are unavailable)
6. Conversations persist as sessions in `data/sessions.sqlite` — every turn is saved with its retrieved-node citations, indexed into txtai as a `session_turn` document, and the chat tab auto-restores the most recent session (gems, intention, history, last 200 traces) on open. Existing stores migrate in place: `rag_messages` gains its `citations` column automatically on next launch
7. Answers are grounded in actual signatures and verified examples. Grounded turns run under a strict anti-hallucination prompt contract: every method, class, module, and code example in the retrieved context carries a sequential `[C1]`-style citation tag (source file included), replies must cite those tags inline and close with a `### Citations` summary, and only APIs present in the citations may be used — unsupported capabilities are stated explicitly, with missing logic supplied as clearly-distinguished custom application Ruby. Turns without a grounded context (no gems selected) run as ordinary ungrounded chat; the contract binds grounded answers, it does not refuse ungrounded ones
8. Export writes the session transcript to `output/chat_exports/graphrag_chat_<timestamp>.md` and `.json` containing intention, gems, conversation history, and per-turn grounded citations (a `> **Grounded Citations:**` list in markdown, a `citations` array in JSON)

## Common Development Tasks

### Installation and Setup
```bash
# Install dependencies with uv (recommended)
uv sync --dev

# Or with pip
pip install -e .
```

### Environment Setup
Required environment variables for full functionality:
```bash
export OPENROUTER_API_KEY="your_key"     # For LLM classification (default provider; MISTRAL_API_KEY when RUBYGEMDB_PROVIDER=mistral)
export CONTEXT7_API_KEY="your_key"        # For cheatsheet generation
```

Optional GitHub token for `import-stars` (raises API rate limits; falls back to `gh auth token` when unset):
```bash
export GITHUB_TOKEN="your_github_token"
```

Optional Langfuse tracing & observability (disabled when keys are unset or `LANGFUSE_ENABLED=false`):
```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."    # Both keys must be set to enable tracing
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_HOST="http://localhost:3000"  # or LANGFUSE_BASE_URL; default https://cloud.langfuse.com
# export LANGFUSE_ENABLED="true"          # explicit enable; =false disables even with keys set
```
When configured, classification, LLM calls, hybrid search, GraphRAG retrieval, agent chat turns, and MCP GraphRAG tools emit Langfuse spans (`services/tracing.py`); the CLI flushes them when a command exits. When disabled, traced blocks yield no-op observations and the Langfuse client is never initialized.

Optional retrieval-quality stages (off by default; GGUF models download lazily from Hugging Face into `data/models/`):
```bash
export QUERY_EXPANSION_ENABLED="true"     # Local GGUF query expansion
export RERANKER_ENABLED="true"            # Local GGUF cross-encoder reranking
```
Tunables: `QUERY_EXPANDER_REPO_ID`/`_FILENAME`/`_N_CTX`/`_N_THREADS`, `RERANKER_REPO_ID`/`_FILENAME`/`_N_CTX`/`_N_THREADS`, `RERANKER_CANDIDATE_MULTIPLIER` (default 3, pool clamped 15–50), `MODELS_CACHE_DIR`. Both stages fall back to baseline retrieval when models are disabled, missing, or fail.

Optional embedding cache retention:
```bash
export EMBEDDINGS_CACHE_MAX_ENTRIES="100000"  # max entries before oldest evicted (default: 100,000)
export EMBEDDINGS_CACHE_TTL_DAYS="180"         # TTL in days for vector records (default: 180)
```

Optional agent output cap:
```bash
export AGENT_MAX_TOKENS="4096"                 # max tokens for GraphRAG agent responses (default: 4096)
```
Raise `AGENT_MAX_TOKENS` when long, citation-tagged implementations truncate — the default cap was introduced with the citations feature (responses were previously uncapped).

### Running the CLI Classifier
Process a CSV of gem names and generate YAML output reports:
```bash
# As installed package
rubygemdb gems-inventory.csv --out output/

# Direct module execution
python -m rubygemdb gems-inventory.csv --out output/

# With uv
uv run rubygemdb gems-inventory.csv --out output/

# Fresh start reclassification with embedding cache purge
uv run rubygemdb reclassify --clear-embeddings-cache --apply-prune

# Interactive candidate selection before reclassifying
uv run rubygemdb reclassify --select-prune --embed

# Prune gems parsed from an approved markdown report, then rebuild
uv run rubygemdb reclassify --prune-list data/prune-report.md --embed
```

### Importing GitHub Starred Gems
Fetch starred Ruby repositories, resolve them to published RubyGems (repo-name match or remote gemspec inspection), enrich inventory records with source repos and Context7 IDs, infer GenAI tooling roles, and run an approval-gated prune audit:
```bash
uv run rubygemdb import-stars --dry-run          # safe preview: no SQLite or disk writes
uv run rubygemdb import-stars --classify --embed # import, classify, and build the vector index
uv run rubygemdb import-stars --select-prune     # interactively multi-select prune candidates
uv run rubygemdb import-stars --apply-prune      # import and auto-prune stale/overlapping gems
uv run rubygemdb import-stars --user username --language Ruby  # custom GitHub user or language filter
uv run rubygemdb import-stars --prune-list prune.txt           # prune only approved gems from a file or report
```

### Interactive Gem Pruning
Audit the inventory for stale (3+ years without release) or overlapping gems and interactively choose which to remove — dual-engine selector (`gum choose --no-limit`, native Textual `SelectionList` fallback). Candidates come from an automated audit, a markdown report (`data/prune-report.md`), or a plain text list:
```bash
uv run rubygemdb prune                           # interactive multi-selector over audit candidates
uv run rubygemdb prune data/prune-report.md      # use candidates from a markdown report
uv run rubygemdb prune --backend gum             # force gum; --backend textual forces SelectionList
uv run rubygemdb prune --dry-run                 # simulate selection without deleting
uv run rubygemdb prune --all                     # prune all candidates without prompting
uv run rubygemdb prune --prune-list prune.txt    # prune gems listed in a file or report
uv run rubygemdb prune --output-list saved.txt   # save interactive selections for reuse
```

### Composing Gem Stacks
Turn a context query into a curated, layered stack manifest (substrate -> quality) plus a Gemfile snippet — two-stage retrieval and second-pass curation (LLM synthesis or offline fallback) with category density limits and task-specific role descriptions, capped by `--max-gems` (default 10):
```bash
uv run rubygemdb stack "CLI data pipeline tool" --max-gems 6
```
The MCP `compose_stack(query, max_gems=10)` tool exposes the same refined selection.

### Managing the Universal Embedding Cache
Inspect, prune, and clear vector entries:
```bash
uv run rubygemdb cache stats                           # Rich table of vector counts, models, disk sizes
uv run rubygemdb cache stats --model "model_name"     # filter the table by embedding model
uv run rubygemdb cache clear                           # purge vectors and reclaim disk space via VACUUM
uv run rubygemdb cache clear --model "model_name"     # clear vectors for a single model
uv run rubygemdb cache prune                           # prune expired vectors (>180d) and cap at 100k
uv run rubygemdb cache prune --max-age-days 90 --max-entries 50000  # custom retention limits
```

Pass `--no-vacuum` on `clear` or `prune` to skip physical SQLite page reclamation.

### Running the TUI Explorer
Launch interactive terminal UI for browsing and managing classified gems:
```bash
# As installed package
rubygemdb-tui [optional-gems-inventory.csv]

# Direct module execution  
python -m rubygemdb.ui.tui [optional-gems-inventory.csv]

# With uv
uv run rubygemdb-tui [optional-gems-inventory.csv]
```

### Running the MCP Server
Expose the taxonomy, classified gems, and stack composition to coding agents (stdio by default, or a background SSE daemon):
```bash
# Stdio transport (default, for host agents such as Claude Code)
uv run rubygemdb-mcp

# Background SSE daemon lifecycle
uv run rubygemdb-mcp --daemon start --port 8011
uv run rubygemdb-mcp --daemon status
uv run rubygemdb-mcp --daemon stop
```

Catalog tools (`search_gems`, `get_gem`, `compose_stack`, `list_categories`, `build_gem_graphrag`, `query_gem_graphrag`), resources (`rubygemdb://taxonomy`, `rubygemdb://stack-seeds`), and prompts (`design-ruby-stack`, `gem-architect-rag`) are strictly read-only against the gem database: SQLite connections open `mode=ro` and every mutating `SQLiteStorage` method raises `PermissionError`. Session tools (`list_rag_sessions`, `get_rag_session`, `resume_rag_session`, `export_rag_session_transcript`, `delete_rag_session`) write only to the separate `data/sessions.sqlite` store. `build_gem_graphrag` and `query_gem_graphrag` accept an optional `session_id` (auto-generated when omitted) and return the `session_id` they recorded the turn in; `query_gem_graphrag` results also carry a top-level `citations` list (id, gem, file, node_id, node_type, symbol, signature, summary) for every retrieved symbol and example. The daemon writes `data/mcp.pid`, a `data/mcp.json` host/port sidecar, and `data/mcp.log`.

### Agent Skill Deployment (`rubygemdb install-skill`)
Deploy or symlink the repository-canonical agent skill (`.skills/rubygemdb/`) to `~/.agents/skills/rubygemdb`:
```bash
uv run rubygemdb install-skill                             # copy SKILL.md and scripts/gem.py to ~/.agents/skills/rubygemdb
uv run rubygemdb install-skill --link                      # symlink the skill directory
uv run rubygemdb install-skill --target /path/to/skills    # custom destination directory
```

### Development Workflow
```bash
# Lint code
uv run ruff check src/rubygemdb/

# Type checking
uv run mypy src/rubygemdb/

# Run formatter
uv run ruff format src/rubygemdb/

# Test with sample data
uv run rubygemdb data/gems-inventory.csv --out test-output/
```

## Classification Logic

One of the 21 categories wins by best score; two thresholds then decide LLM override:

### Heuristic Signals
- **Keyword rules** (from `models/categories.py`): gem-name token matches score 0.8; dependency substring matches score 0.65
- **Description scoring**: RubyGems `info` term hits against category keywords/descriptions (≤ 0.6)
- **Embedding similarity**: txtai vectors vs category description vectors (≤ 0.6)
- **Sub-category faceting**: `detect_sub_categories` scores the winning category's facet vocabulary (example gems and sub-slug tokens) against name, description, and dependencies; kept facets persist in `classified_gems.sub_categories`
- **Defaults**: unmatched gems fall to `core_extensions` at 0.4 confidence

### Confidence Thresholds
- Heuristic confidence ≥ 0.7: keep the heuristic pick (the LLM still generates the agent description)
- < 0.7: the LLM's category (>0.6 confidence) may override the heuristic pick
- LLM confidence threshold: 0.6 to override heuristics

## Input Data Format

CSV files must contain either:
- `name` column (primary) or `gem` column  
- Optional columns: `group`, `category`, `description`, `homepage`, `source_code_uri`, `context7_id`

## Output Structure

### YAML Files
Classification results saved as `{category}.yaml` in output directory:
```yaml
- name: gem_name
  classification:
    primary: category_name
    confidence: 0.85
    sub_categories: [terminal_styling]
  role:
    description: "Gem description from RubyGems"
    attaches_to: "category_prefix"
  risks:
    invasiveness: 1-5
    coupling: 1-4  
    abstraction_leak: "low|medium|high"
  signals:
    rails: boolean
    external_io: boolean
    native_ext: boolean
```

## Storage and Caching Strategy

### Primary Storage
- **SQLite Database**: `data/rubygemdb.sqlite` - Central storage for inventory and classified gems; the `classified_gems` table carries a `sub_categories` column (auto-added by migration on existing databases)
- **Session Store**: `data/sessions.sqlite` - Separate SQLite database for persistent GraphRAG sessions (`rag_sessions`, `rag_messages`, `rag_traces` tables, auto-created on first use by `storage/session_storage.py`); `rag_messages` carries a `citations` column, auto-added by `ALTER TABLE` migration on existing stores
- **Read-Only Mode**: `SQLiteStorage(read_only=True, db_path=...)` opens `file:...?mode=ro` URI connections, skips schema init, and raises `PermissionError` on every mutating method — this is what the MCP server uses for the gem catalog; `get_classified_gem(name)` / `get_inventory_gem(name)` back the `get_gem` MCP tool
- **YAML Output**: Per-category files in output directory for batch processing results

### Caching Layers
- **API Cache**: `data/cache/gem_cache.json` - Persistent cache for RubyGems API responses
- **LLM Cache**: `data/cache/llm_cache.json` - SHA256-hashed prompt caching for LLM responses (temperature 0)
- **Results Cache**: `data/classified_gems.json` - Persists TUI state between sessions
- **Embedding Cache**: `data/cache/embeddings_cache.sqlite` - Universal content-addressable vector cache (SHA-256 of model path + text, `services/embedding_cache.py`) shared by GraphRAG indexing, classifier embedding scoring, and agent sessions via `wrap_embeddings()`; repeat vectorization of known texts skips the embedding model. Configurable capacity and TTL retention policies (`settings.embeddings_cache_max_entries` default 100,000; `settings.embeddings_cache_ttl_days` default 180), opportunistic inline eviction during `put_many`, and operator CLI management via `rubygemdb cache {stats, clear, prune}` and `rubygemdb reclassify --clear-embeddings-cache`
- **GGUF Model Cache**: `data/models/` - Query-expansion and reranker weights, lazily downloaded from Hugging Face via `services/model_manager.py`

## TUI Interface Features

### Navigation
- Tab between gem list and details panels
- Filter by classification category
- Search gems by name
- Batch operations for reclassification

## Context7 Integration
- Generate usage cheatsheets for selected gems
- Saves to `output/cheatsheets/`
- Requires CONTEXT7_API_KEY environment variable
- Library IDs are repo-anchored: `verify_library` canonicalizes IDs to `/owner/repo` form derived from the gem's source/homepage repo URL, preferring an exact repo-path match over the first search result

## Error Handling Patterns

### Robust API Calls
- Linear retry backoff: 1s, 2s, 3s between gem-fetch attempts (services/rubygems.py); LLM calls are single-shot with a 15s timeout (services/llm.py)
- fetch_gem_info accepts a retries parameter (default 3, services/rubygems.py); LLM calls have no retry knob
- Failed API calls return None and classification proceeds on heuristics (rubygems.py, llm.py); embedding scoring falls back to keyword + description signals when ollama is down (embedding_scorer.py:57-74)

### Rate Limiting
- 0.1s between RubyGems requests (hardcoded, services/rubygems.py:13; the RATE_LIMIT_DELAY setting is currently unread)
- LLM calls have no delay (single-shot, 15s timeout); the `LLM_RATE_DELAY` setting is currently unread
- `LLM_BATCH_SIZE = 10` for efficient bulk processing

## Debugging Tips

### API Issues
- Check `data/cache/gem_cache.json` for cached responses
- Verify RubyGems API connectivity: `curl "https://rubygems.org/api/v1/gems/{name}.json"`
- Monitor rate limiting with request delays

### LLM Classification Problems  
- Review prompts in `llm_cache.json` using prompt hash
- Check LLM provider configuration and API key (default: OPENROUTER_API_KEY; MISTRAL_API_KEY when RUBYGEMDB_PROVIDER=mistral)
- Validate JSON parsing of LLM responses

### TUI Navigation Issues
- Ensure CSV has proper column headers (`name` or `gem`)
- Check file paths are absolute when loading caches
- Verify Textual event handling for key bindings