# RubyGemDB

RubyGemDB is a specialized tool for analyzing and classifying Ruby gems into architectural patterns. It leverages heuristic-based analysis (name, dependencies, and metadata) and LLM fallback (via Mistral AI/Devstral) to categorize gems, providing insights into their role and potential risks within an ecosystem.

## Project Overview

- **Architectural Categories**: `runtime_spine`, `cli_terminal_ui`, `storage_persistence`, `async_networking_orchestration`, `ai_nlp`, `data_processing`, `retrieval_similarity_fuzzy`, `algorithms_knowledge_structures`, `validation_types`, `parsing_encoding`, `debugging_introspection`, `mcp_tooling`.
- **Modular Engine**: Structured under `src/rubygemdb/` for scalability:
  - `core/config.py`: Centralized settings using `pydantic-settings` and `.env` support.
  - `models/gem.py`: Robust data models for gem entries, classifications, and risks.
  - `services/`: Specialized services for RubyGems API, LLM classification, and Context7 integration.
  - `storage/`: Multi-backend storage (SQLite + JSON) for inventory and results.
- **Interactive Explorer (TUI)**: A feature-rich Textual-based UI for browsing, filtering, and managing gem classifications.

- **Codebase Memory MCP**: Native integration with the codebase-memory-mcp server to ground LLM-generated integration plans in the actual architectural structure of target projects.
- **Other Steve Prompt Architect**: A highly aggressive system persona that intercepts raw user queries, strips out conversational slop, and generates concrete, SFL-compliant queries for the txtai vector database.
- **Context7 Integration**: Deep integration with Context7 for library search, manual ID entry, and batch cheatsheet generation for gems and their dependencies.

## Building and Running

### Environment Setup

1.  **Python Dependencies** (Python >= 3.14):
    ```bash
    uv pip install -e .
    # Or for development
    uv sync
    ```
2.  **API Keys (Required for full functionality)**:
    Create a `.env` file or export the following variables:
    ```bash
    DEVSTRAL_API_KEY="your_mistral_key" # For LLM classification
    CONTEXT7_API_KEY="your_context7_key" # For cheatsheet generation in TUI
    ```

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

## Development Conventions

### Caching Strategy
- `rubygemdb.sqlite`: Central SQLite database for inventory, verified results, and manual metadata updates (Source URIs, Context7 IDs).
- `gem_cache.json`: Caches raw responses from the RubyGems API.
- `llm_cache.json`: Caches prompts and responses from the LLM classifier.
- `classified_gems.json`: A unified JSON export of all classified gems for TUI consumption.

### Classification Logic
- **Heuristics First**: Gems are first matched against known patterns (e.g., `active_support` -> `runtime_spine`).
- **LLM Fallback**: If the heuristic confidence is below 0.7, the tool calls the LLM (Mistral/Devstral) for a more nuanced analysis.
- **Confidence Threshold**: LLM results require a confidence > 0.6 to override initial heuristics.
- **Metadata Update**: Users can manually refine Context7 IDs and Source URIs directly from the TUI, which updates the SQLite storage.

### Key Data Files
- `data/gems-inventory.csv`: The primary input format for populating the database.
- `data/rubygemdb.sqlite`: Persistent storage for the verified gem inventory.
- `output/cheatsheets/`: Directory for generated Context7 cheatsheets.
