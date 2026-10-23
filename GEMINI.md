# RubyGemDB

RubyGemDB is a specialized tool for analyzing and classifying Ruby gems into architectural patterns. It leverages heuristic-based analysis (name, dependencies, and metadata) and LLM fallback (via Mistral AI/Devstral) to categorize gems, providing insights into their role and potential risks within an ecosystem.

## Project Overview

- **Functional Categories** (21, defined in `src/rubygemdb/models/categories.py`): `web_frameworks`, `http_networking`, `servers_concurrency`, `background_jobs`, `persistence`, `document_parsing`, `document_generation`, `static_site_generation`, `cli_libraries`, `testing_qa`, `code_quality_typing`, `runtime_validation`, `developer_tools`, `ai_llm`, `mcp_tooling`, `text_search`, `security_auth`, `media_processing`, `gui_desktop`, `native_bindings`, `core_extensions`.
- **Stack Layers**: each category carries a layer (substrate, plumbing, composition, quality) plus curated base-gem seeds (dotenv, drydock, pry, rubocop, journald-logger), enabling layered gem-stack recommendations for back-end tooling design.
- **Modular Engine**: Structured under `src/rubygemdb/` for scalability:
  - `core/config.py`: Centralized settings using `pydantic-settings` and `.env` support.
  - `models/gem.py`: Robust data models for gem entries, classifications, and risks.
  - `models/session.py`: GraphRAG session models (`RAGSession`, `RAGMessage`, `RAGTrace`).
  - `services/`: Specialized services for RubyGems API, LLM classification, Context7 integration, Langfuse tracing (`services/tracing.py`), and a universal SQLite embedding cache (`services/embedding_cache.py`).
  - `storage/`: Multi-backend storage (SQLite + JSON) for inventory and results, plus a persistent GraphRAG session store (`storage/session_storage.py`).
- **Persistent GraphRAG Sessions**: Conversations, retrieved-node citations, and execution traces persist in `data/sessions.sqlite` (`rag_sessions`, `rag_messages`, `rag_traces` tables, auto-created on first use). The TUI chat tab auto-restores the most recent session on open and offers New Session / Delete Session buttons; every turn is indexed into txtai as a `session_turn` document.
- **Interactive Explorer (TUI)**: A feature-rich Textual-based UI for browsing, filtering, and managing gem classifications.
- **Contextual GraphRAG (TUI Chat)**: Spins up an ephemeral container (`rubygemdb-indexer:latest`) to extract AST symbol hierarchies (`rubydex`) and structured documentation (`decode`) from shallow-cloned gem repositories (with `.gem` unpack fallback), embedding results into ephemeral `txtai` vector stores with two-stage grounded retrieval (optionally local-GGUF query expansion and cross-encoder reranking, both off by default), persistent sessions, and markdown/JSON transcript export.

- **Codebase Memory MCP**: Native integration with the codebase-memory-mcp server to ground LLM-generated integration plans in the actual architectural structure of target projects.
- **Other Steve Prompt Architect**: A highly aggressive system persona that intercepts raw user queries, strips out conversational slop, and generates concrete, SFL-compliant queries for the txtai vector database.
- **Context7 Integration**: Deep integration with Context7 for library search, manual ID entry, and batch cheatsheet generation for gems and their dependencies. Library IDs are canonicalized to `/owner/repo` form, derived from source/homepage repo URLs when possible.

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
    MISTRAL_API_KEY="your_mistral_key" # For LLM classification (read by core/config.py)
    CONTEXT7_API_KEY="your_context7_key" # For cheatsheet generation in TUI
    LANGFUSE_PUBLIC_KEY="pk-lf-..." # Optional Langfuse tracing & observability
    LANGFUSE_SECRET_KEY="sk-lf-..."
    LANGFUSE_BASE_URL="http://localhost:3000" # or LANGFUSE_HOST
    LANGFUSE_ENABLED="true"
    ```

    Optional retrieval-quality stages (both off by default): set `QUERY_EXPANSION_ENABLED` / `RERANKER_ENABLED` to enable local GGUF query expansion and cross-encoder reranking. Models download lazily from Hugging Face into `data/models/`; retrieval degrades to baseline when they are unavailable.

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
  Builds an approval-gated prune report (`data/prune-report.md`), then wipes and rebuilds all classified data under the 21-category taxonomy. A dated SQLite backup is written first.
  ```bash
  uv run rubygemdb reclassify --apply-prune --embed --out output/
  ```
- **Gem-Stack Composition**:
  Turns a context query into a layered stack manifest (substrate -> quality) plus a Gemfile snippet, starting from curated base picks (dotenv, drydock, pry, rubocop, journald-logger).
  ```bash
  uv run rubygemdb stack "CLI data pipeline tool" --out stacks/
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
  Other flags: `--transport stdio|sse` (default `stdio`), `--host` (default `127.0.0.1`), `--port` (default `8000`), `--foreground`, `--pid-file` (default `data/mcp.pid`), `--log-file` (default `data/mcp.log`). Catalog tools are strictly read-only: SQLite `mode=ro` connections; mutating storage methods raise `PermissionError`. Session tools write only to the separate `data/sessions.sqlite` store. `build_gem_graphrag` and `query_gem_graphrag` accept an optional `session_id` (auto-generated when omitted) and return the `session_id` they recorded the turn in.

## Development Conventions

### Caching Strategy
- `rubygemdb.sqlite`: Central SQLite database for inventory, verified results, and manual metadata updates (Source URIs, Context7 IDs).
- `data/cache/gem_cache.json`: Caches raw responses from the RubyGems API.
- `data/cache/llm_cache.json`: Caches prompts and responses from the LLM classifier.
- `data/cache/embeddings_cache.sqlite`: Universal content-addressable vector cache (SHA-256 of model path + text) shared by GraphRAG indexing, classifier embedding scoring, and agent sessions — repeat vectorization of known texts skips the embedding model entirely. No TTL or size cap; the file grows until `EmbeddingCache.clear()` is called.
- `classified_gems.json`: A unified JSON export of all classified gems for TUI consumption.
- `sessions.sqlite`: Persistent GraphRAG session store (`data/sessions.sqlite`; `rag_sessions`, `rag_messages`, `rag_traces` tables, auto-created on first use).
- `data/models/`: Local GGUF model cache for query expansion and reranking (lazily downloaded from Hugging Face).

### Classification Logic
- **Heuristics First**: Gems are first matched against keyword rules and scored by description text and txtai embedding similarity against category descriptions (e.g., `nokogiri` -> `document_parsing`).
- **Sub-Category Facets**: after the primary category wins, `detect_sub_categories` matches that category's `sub_categories` vocabulary (example gems, sub-slug tokens) against the gem's name, description, and dependencies. Facets persist in the `classified_gems.sub_categories` column and appear in YAML/TUI output.
- **LLM Fallback**: the LLM (Mistral/Devstral) is always consulted for the agent description; when heuristic confidence is below 0.7, its category may override the heuristic pick.
- **Confidence Threshold**: LLM results require a confidence > 0.6 to override initial heuristics.
- **Metadata Update**: Users can manually refine Context7 IDs and Source URIs directly from the TUI, which updates the SQLite storage.

### Key Data Files
- `data/gems-inventory.csv`: The primary input format for populating the database.
- `data/rubygemdb.sqlite`: Persistent storage for the verified gem inventory.
- `data/sessions.sqlite`: Persistent GraphRAG sessions, chat messages, and execution traces (separate from the gem catalog).
- `output/cheatsheets/`: Directory for generated Context7 cheatsheets.
