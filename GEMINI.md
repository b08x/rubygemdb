# RubyGemDB

RubyGemDB is a specialized tool for analyzing and classifying Ruby gems into architectural patterns. It leverages heuristic-based analysis (name, dependencies, and metadata) and LLM fallback via the configured provider (default openrouter, config.py:18-19) to categorize gems, providing insights into their role and potential risks within an ecosystem.

## Project Overview

- **Functional Categories** (21, defined in `src/rubygemdb/models/categories.py`): `web_frameworks`, `http_networking`, `servers_concurrency`, `background_jobs`, `persistence`, `document_parsing`, `document_generation`, `static_site_generation`, `cli_libraries`, `testing_qa`, `code_quality_typing`, `runtime_validation`, `developer_tools`, `ai_llm`, `mcp_tooling`, `text_search`, `security_auth`, `media_processing`, `gui_desktop`, `native_bindings`, `core_extensions`.
- **Stack Layers**: each category carries a layer (substrate, plumbing, composition, quality) plus curated base-gem seeds (dotenv, drydock, pry, rubocop, journald-logger), enabling layered gem-stack recommendations for back-end tooling design.
- **Modular Engine**: package layout under `src/rubygemdb/` — core/, models/, services/, storage/, ui/:
  - `core/config.py`: Centralized settings using `pydantic-settings` and `.env` support.
  - `models/gem.py`: Pydantic data models for gem entries, classifications, and risks.
  - `models/session.py`: GraphRAG session models (`RAGSession`, `RAGMessage`, `RAGTrace`).
  - `services/`: Specialized services for RubyGems API, LLM classification, Context7 integration, Langfuse tracing (`services/tracing.py`), and a universal SQLite embedding cache (`services/embedding_cache.py`).
  - `storage/`: Multi-backend storage (SQLite + JSON) for inventory and results, plus a persistent GraphRAG session store (`storage/session_storage.py`).
- **Persistent GraphRAG Sessions**: Conversations, retrieved-node citations, and execution traces persist in `data/sessions.sqlite` (`rag_sessions`, `rag_messages`, `rag_traces` tables, auto-created on first use). The TUI chat tab auto-restores the most recent session on open and offers New Session / Delete Session buttons; every turn is indexed into txtai as a `session_turn` document.
- **Interactive Explorer (TUI)**: A Textual-based TUI (ui/tui.py) with 13 key bindings (tui.py), category filtering, and a GraphRAG chat tab.
- **Contextual GraphRAG (TUI Chat)**: Spins up an ephemeral container (`rubygemdb-indexer:latest`) to extract AST symbol hierarchies (`rubydex`) and structured documentation (`decode`) from shallow-cloned gem repositories (with `.gem` unpack fallback), embedding results into ephemeral `txtai` vector stores with two-stage grounded retrieval (optionally local-GGUF query expansion and cross-encoder reranking, both off by default), persistent sessions, and markdown/JSON transcript export. Grounded answers are citation-tagged (`[C1]`-style, source file included) and prompt-constrained to the APIs the citations contain — unsupported capabilities are stated explicitly and missing logic is supplied as clearly-distinguished custom Ruby; turns without a grounded context run as ordinary ungrounded chat. Agent output is capped by `AGENT_MAX_TOKENS` (default 4096).

Agent-side integrations:

- **Codebase Memory MCP**: Native integration with the codebase-memory-mcp server to ground LLM-generated integration plans in the actual architectural structure of target projects.
- **Query Expansion (optional)**: QUERY_EXPANSION_ENABLED=true expands queries into lexical/vector variations via a local GGUF model (off by default; degrades to baseline retrieval)
- **Context7 Integration**: services/context7.py provides library search, manual ID entry, and batch cheatsheet generation; IDs canonicalize to /owner/repo form

## Building and Running

### Environment Setup

1.  **Python Dependencies** (Python >= 3.14):
    ```bash
    # Standard installation (CPU)
    uv sync --extra cpu

    # Optional CUDA support (NVIDIA GPU acceleration)
    uv sync --extra cuda
    ```
2.  **API Keys (Required for full functionality)**:
    Create a `.env` file or export the following variables:
    ```bash
    OPENROUTER_API_KEY="your_openrouter_key" # default provider key (config.py:180-191); MISTRAL_API_KEY only when RUBYGEMDB_PROVIDER=mistral
    CONTEXT7_API_KEY="your_context7_key" # For cheatsheet generation in TUI
    LANGFUSE_PUBLIC_KEY="pk-lf-..." # Optional Langfuse tracing & observability
    LANGFUSE_SECRET_KEY="sk-lf-..."
    LANGFUSE_BASE_URL="http://localhost:3000" # or LANGFUSE_HOST
    LANGFUSE_ENABLED="true"
    GITHUB_TOKEN="your_github_token" # Optional: import-stars auth (falls back to `gh auth token` when unset)
    ```

    Optional retrieval-quality stages (both off by default): set `QUERY_EXPANSION_ENABLED` / `RERANKER_ENABLED` to enable local GGUF query expansion and cross-encoder reranking. Models download lazily from Hugging Face into `data/models/`; retrieval degrades to baseline when they are unavailable.

    Optional embedding cache retention: configure `EMBEDDINGS_CACHE_MAX_ENTRIES` (default: 100,000) and `EMBEDDINGS_CACHE_TTL_DAYS` (default: 180) to govern universal vector cache retention.

    Optional agent output cap: `AGENT_MAX_TOKENS` (default: 4096) limits GraphRAG agent response length — raise it when long, citation-tagged implementations truncate.

### Execution Commands

- **Batch Classifier (CLI)**:
  Processes a CSV of gem names and stores results in SQLite + YAML files.
  ```bash
  uv run rubygemdb data/gems-inventory.csv --out output/
  ```
- **Gem Explorer (TUI)**:
  Launches the interactive explorer to browse gems and manage Context7 IDs.
  ```bash
  uv run rubygemdb-tui [optional-inventory.csv]
  ```
  *Note: The CSV is only required for the first run to populate the SQLite database.*
- **Reclassification (fresh start)**:
  Builds an approval-gated prune report (`data/prune-report.md`), then wipes and rebuilds all classified data under the 21-category taxonomy. Supports interactive multi-selection of candidates via CLI. A dated SQLite backup is written first. Pass `--clear-embeddings-cache` to also purge and vacuum the vector cache.
  ```bash
  uv run rubygemdb reclassify --select-prune --embed # interactive multi-select candidates to prune
  uv run rubygemdb reclassify --apply-prune --embed --out output/
  uv run rubygemdb reclassify --clear-embeddings-cache --apply-prune
  uv run rubygemdb reclassify --prune-list data/prune-report.md --embed # prune approved gems from a markdown report
  ```
- **Import GitHub Starred Gems (CLI)**:
  Fetches starred Ruby repositories from GitHub, resolves them to published RubyGems, enriches existing records with source repositories/Context7 IDs, infers GenAI backend tooling roles (`genai_uses`), and runs an approval-gated prune audit:
  ```bash
  uv run rubygemdb import-stars --dry-run          # dry-run preview (no SQLite or disk modifications)
  uv run rubygemdb import-stars --classify --embed # full import, taxonomy classification, and vector indexing
  uv run rubygemdb import-stars --select-prune     # interactively multi-select prune candidates from audit
  uv run rubygemdb import-stars --apply-prune      # import and prune stale/overlapping gems
  uv run rubygemdb import-stars --user username --language Ruby  # custom GitHub user or language filter
  uv run rubygemdb import-stars --prune-list prune.txt           # prune only approved gems from a file or report
  ```
- **Interactive Gem Pruning (`rubygemdb prune`)**:
  Audit inventory for stale or overlapping gems and interactively choose gems to remove using a dual-engine CLI multi-selector (`gum choose --no-limit` or Textual `SelectionList` fallback). Accepts candidates from plain text files, markdown reports (`data/prune-report.md`), or an automated inventory audit:
  ```bash
  uv run rubygemdb prune                           # launch interactive multi-selector for audit candidates
  uv run rubygemdb prune data/prune-report.md      # launch multi-selector using candidates from markdown report
  uv run rubygemdb prune --backend textual         # force Textual SelectionList backend
  uv run rubygemdb prune --backend gum             # force Charmbracelet gum backend
  uv run rubygemdb prune --dry-run                 # simulate selection without deleting
  uv run rubygemdb prune --all                     # prune all candidates without prompting
  uv run rubygemdb prune --prune-list prune.txt    # prune gems listed in file or report
  uv run rubygemdb prune --output-list saved.txt   # save chosen gems to file
  ```
- **Embedding Cache Management (`rubygemdb cache`)**:
  Inspect, prune, and clear the shared vector embedding cache:
  ```bash
  uv run rubygemdb cache stats                           # formatted table of vector counts, models, and disk sizes
  uv run rubygemdb cache stats --model "model_name"     # filter the table by embedding model
  uv run rubygemdb cache clear                           # purge all vectors and reclaim host disk space via VACUUM
  uv run rubygemdb cache clear --model "model_name"     # clear vectors for a specific model only
  uv run rubygemdb cache prune                           # prune expired vectors (>180d) and cap at 100k entries
  uv run rubygemdb cache prune --max-age-days 90 --max-entries 50000  # custom retention limits
  ```
  Pass `--no-vacuum` on `clear` or `prune` to skip physical SQLite page reclamation.
- **Gem-Stack Composition**:
  Turns a context query into a curated, layered stack manifest (substrate -> quality) plus a Gemfile snippet using two-stage retrieval and second-pass curation (LLM synthesis or offline fallback), capped by `--max-gems` (default 10).
  ```bash
  uv run rubygemdb stack "CLI data pipeline tool" --max-gems 8 --out stacks/
  ```
- **Model Context Protocol (MCP) Server**:
  Native MCP server running over stdio by default, or as a detached SSE daemon in the background. Exposes catalog tools (`search_gems`, `get_gem`, `compose_stack`, `list_categories`, `build_gem_graphrag`, `query_gem_graphrag`), session tools (`list_rag_sessions`, `get_rag_session`, `resume_rag_session`, `export_rag_session_transcript`, `delete_rag_session`), resources (`rubygemdb://taxonomy`, `rubygemdb://stack-seeds`), and prompts (`design-ruby-stack`, `gem-architect-rag`).
  ```bash
  # Stdio mode (default, for host agents)
  uv run rubygemdb-mcp

  # Background SSE daemon lifecycle
  uv run rubygemdb-mcp --daemon start --port 8011
  uv run rubygemdb-mcp --daemon status
  uv run rubygemdb-mcp --daemon stop
  uv run rubygemdb-mcp --sse                 # shortcut for --daemon start
  ```
  Other flags: `--transport stdio|sse` (default `stdio`), `--host` (default `127.0.0.1`), `--port` (default `8000`), `--foreground`, `--pid-file` (default `data/mcp.pid`), `--log-file` (default `data/mcp.log`). Catalog tools are strictly read-only: SQLite `mode=ro` connections; mutating storage methods raise `PermissionError`. Session tools write only to the separate `data/sessions.sqlite` store. `build_gem_graphrag` and `query_gem_graphrag` accept an optional `session_id` (auto-generated when omitted) and return the `session_id` they recorded the turn in; `query_gem_graphrag` results also carry a top-level `citations` list for every retrieved symbol and example. `compose_stack` accepts `max_gems` (default 10) to cap stack density.
- **Agent Skill Deployment (`rubygemdb install-skill`)**:
  Deploy or symlink the repository-canonical agent skill (`.skills/rubygemdb/`) to `~/.agents/skills/rubygemdb`:
  ```bash
  uv run rubygemdb install-skill                             # copy SKILL.md and scripts/gem.py to ~/.agents/skills/rubygemdb
  uv run rubygemdb install-skill --link                      # symlink the skill directory
  uv run rubygemdb install-skill --target /path/to/skills    # custom destination directory
  ```

## Classification Logic
- **Heuristics First**: Gems are first matched against keyword rules and scored by description text and txtai embedding similarity against category descriptions (e.g., `nokogiri` -> `document_parsing`).
- **Sub-Category Facets**: after the primary category wins, `detect_sub_categories` matches that category's `sub_categories` vocabulary (example gems, sub-slug tokens) against the gem's name, description, and dependencies. Facets persist in the `classified_gems.sub_categories` column and appear in YAML/TUI output.
- **LLM Fallback**: name-keyword matches score 0.8, dependency-only 0.65, description/embedding-only ≤0.6 (default 0.4); the configured LLM (default openrouter/upstage/solar-mini4) is always consulted for the agent description, and when heuristic confidence is below 0.7, its category may override the heuristic pick.
- **Confidence Threshold**: LLM results require a confidence > 0.6 to override initial heuristics.
- **Metadata Update**: Users can manually refine Context7 IDs and Source URIs directly from the TUI, which updates the SQLite storage.

## Caching Strategy
- `rubygemdb.sqlite`: Central SQLite database for inventory, verified results, and manual metadata updates (Source URIs, Context7 IDs).
- `data/cache/gem_cache.json`: Caches raw responses from the RubyGems API.
- `data/cache/llm_cache.json`: Caches prompts and responses from the LLM classifier.
- `data/cache/embeddings_cache.sqlite`: Universal content-addressable vector cache (SHA-256 of model path + text) shared by GraphRAG indexing, classifier embedding scoring, and agent sessions — repeat vectorization of known texts skips the embedding model entirely. Configurable capacity (default 100,000) and TTL retention (default 180 days), opportunistic inline eviction during `put_many`, and operator CLI lifecycle management (`rubygemdb cache`).
- `classified_gems.json`: A unified JSON export of all classified gems for TUI consumption.
- `data/models/`: Local GGUF model cache for query expansion and reranking (lazily downloaded from Hugging Face).

### Key Data Files
- `data/gems-inventory.csv`: The primary input format for populating the database.
- `data/rubygemdb.sqlite`: Persistent storage for the verified gem inventory.
- `data/sessions.sqlite`: Persistent GraphRAG sessions, chat messages, and execution traces (separate from the gem catalog).
- `output/cheatsheets/`: Directory for generated Context7 cheatsheets.
