# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RubyGemDB is a Ruby gem analysis and classification tool that categorizes gems into architectural patterns using heuristics and LLM-based analysis. The system provides both command-line and interactive terminal UI (TUI) interfaces for exploring and understanding Ruby gem ecosystems.

## Architecture

### Core Classification System
- **21 Functional Categories** (defined in `src/rubygemdb/models/categories.py`, each with a stack layer): web_frameworks, http_networking, servers_concurrency, background_jobs, persistence, document_parsing, document_generation, static_site_generation, cli_libraries, testing_qa, code_quality_typing, runtime_validation, developer_tools, ai_llm, mcp_tooling, text_search, security_auth, media_processing, gui_desktop, native_bindings, core_extensions
- **Stack Layers**: every category is tagged substrate / plumbing / composition / quality so results can be presented as a layered stack for back-end tooling design
- **Scoring Pipeline**: keyword rules (name + dependencies) + RubyGems description-text scoring + txtai embedding similarity against category descriptions; best score wins. After the primary category is chosen, a taxonomy-driven pass (`GemClassifier.detect_sub_categories`) matches the category's `sub_categories` vocabulary against the gem's name, description, and dependencies
- **LLM Refinement**: the Mistral/Devstral LLM is always consulted for the agent description; heuristic confidence < 0.7 is what lets its category (confidence > 0.6) override the heuristic pick
- **Confidence Scoring**: Risk assessment based on invasiveness, coupling, and abstraction leak potential

### Data Flow
1. CSV input → RubyGems API → Heuristic classification → LLM enhancement → SQLite/YAML output
2. Caching layers for both API responses (`data/cache/gem_cache.json`) and LLM responses (`llm_cache.json`)
3. Results cached in `data/classified_gems.json` and SQLite database for TUI persistence

### Key Components
```
src/rubygemdb/
├── cli.py              # Command-line interface entry point
├── agent.py            # Advisor wiring: card/intent/reason LLMs + tool-calling agent
├── ui/tui.py          # Interactive Textual-based TUI with gem explorer + advisor chat
├── core/config.py     # Settings and environment variable handling  
├── models/gem.py      # Pydantic data models for gem data
├── models/phases.py   # skeleton/input/processing/output phase mapping (search filters)
├── services/          # Core business logic
│   ├── advisor.py     # Card-driven advisor session state machine
│   ├── advisor_export.py # Markdown + JSON export of advisor sessions
│   ├── hybrid_search.py # SQLite FTS5 + txtai vector search, RRF fusion
│   ├── classifier.py  # Heuristic and LLM classification logic
│   ├── rubygems.py    # RubyGems API client with caching
│   ├── llm.py         # Devstral LLM client for fallback classification
│   └── context7.py    # Context7 API client for cheatsheet generation
└── storage/           # Data persistence layer
    ├── base.py        # Storage interface
    ├── json_storage.py# JSON file storage implementation
    └── sqlite_storage.py # SQLite storage implementation (primary)
```

### Advisor Chat (TUI chat tab)
The chat tab is a card-driven project-brief advisor, not a general chatbot:
1. User submits a project brief; the advisor extracts a structured requirement card — deliverable, inputs, transforms, outputs, constraints, open questions — (may ask one clarifying question when the brief is too vague) and shows it for review
2. On `go`, each searchable slot runs its own hybrid search restricted to its build phase's categories (deliverable → skeleton, inputs → input, transforms → processing, outputs → output; `models/phases.py` is now purely a search-filter taxonomy). Every card item is searched separately and merged by best score
3. The whole stack is proposed at once, grouped by slot, with one-line reasons. Retrieval is hybrid (txtai vectors + SQLite FTS5, RRF K=60): the FTS match is AND-joined before falling back to OR, and the phase's category bucket is narrowed to the categories the query's tokens target (`hybrid_search.narrow_categories`)
4. Stack actions: `accept` (confirm the whole stack), `remove <gem>`, `add <gem>` (manually include an inventoried gem that was never offered), `swap <gem> for <gem>` (within a slot), `retry <slot> <focus>` (re-search a slot; without a named slot the focus infers it from targeted categories), `edit <slot> ...` (change the card; only affected slots re-search), `ask <gem> <question>`, `start over`. Any other message is answered conversationally by the intent LLM, which also suggests the next action
5. Export writes `output/chat_exports/chat_<timestamp>.md` and `.json` with brief, card, stack by slot, and transcript; Clear resets the session

State lives in `services/advisor.py` (`AdvisorSession`, stages brief → card → stack → done); `agent.py` supplies the LLM-backed card extractor, intent parser, reason generator, and the tool-calling agent that answers `ask` questions.

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
export DEVSTRAL_API_KEY="your_key"        # For LLM classification
export CONTEXT7_API_KEY="your_key"        # For cheatsheet generation
```

### Running the CLI Classifier
Process a CSV of gem names and generate YAML output reports:
```bash
# As installed package
rubygemdb gems-inventory.csv --out output/

# Direct module execution
python -m rubygemdb gems-inventory.csv --out output/

# With uv
uv run rubygemdb gems-inventory.csv --out output/
```

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

### Heuristic Signals
- **Keyword rules** (from `models/categories.py`): gem-name token matches score 0.8; dependency substring matches score 0.65
- **Description scoring**: RubyGems `info` term hits against category keywords/descriptions (≤ 0.6)
- **Embedding similarity**: txtai vectors vs category description vectors (≤ 0.6)
- **Sub-category faceting**: `detect_sub_categories` scores the winning category's facet vocabulary (example gems and sub-slug tokens) against name, description, and dependencies; kept facets persist in `classified_gems.sub_categories`
- **Defaults**: unmatched gems fall to `core_extensions` at 0.4 confidence

### Confidence Thresholds
- High confidence (>0.7): Keep heuristic classification (the LLM still generates the agent description)
- Low confidence (<0.7): The LLM's category may override the heuristic pick
- LLM confidence threshold: 0.6 to override heuristics

## Input Data Format

CSV files must contain either:
- `name` column (primary) or `gem` column  
- Optional columns: `group`, `category`, `description`, `homepage`, `source_code_uri`, `context7_id`

## Storage and Caching Strategy

### Primary Storage
- **SQLite Database**: `data/rubygemdb.sqlite` - Central storage for inventory and classified gems; the `classified_gems` table carries a `sub_categories` column (auto-added by migration on existing databases)
- **YAML Output**: Per-category files in output directory for batch processing results

### Caching Layers
- **API Cache**: `data/cache/gem_cache.json` - Persistent cache for RubyGems API responses
- **LLM Cache**: `llm_cache.json` - SHA256-hashed prompt caching for LLM responses (temperature 0)
- **Results Cache**: `data/classified_gems.json` - Persists TUI state between sessions

## TUI Interface Features

### Navigation
- Tab between gem list and details panels
- Filter by classification category
- Search gems by name
- Batch operations for reclassification

### Context7 Integration
- Generate usage cheatsheets for selected gems
- Saves to `/home/b08x/Workspace/Tools/cheatsheets/`
- Requires CONTEXT7_API_KEY environment variable
- Library IDs are repo-anchored: `verify_library` canonicalizes IDs to `/owner/repo` form derived from the gem's source/homepage repo URL, preferring an exact repo-path match over the first search result

## Error Handling Patterns

### Robust API Calls
- Exponential backoff with jitter for rate limiting
- Configurable retry attempts (default: 5 for network, 3 for gems)
- Graceful degradation when APIs are unavailable

### Rate Limiting
- `RATE_LIMIT_DELAY = 0.1` seconds between RubyGems requests
- `LLM_RATE_DELAY = 0.2` seconds between LLM requests  
- `LLM_BATCH_SIZE = 10` for efficient bulk processing

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

## Debugging Tips

### API Issues
- Check `data/cache/gem_cache.json` for cached responses
- Verify RubyGems API connectivity: `curl "https://rubygems.org/api/v1/gems/{name}.json"`
- Monitor rate limiting with request delays

### LLM Classification Problems  
- Review prompts in `llm_cache.json` using prompt hash
- Check Devstral endpoint configuration and API key
- Validate JSON parsing of LLM responses

### TUI Navigation Issues
- Ensure CSV has proper column headers (`name` or `gem`)
- Check file paths are absolute when loading caches
- Verify Textual event handling for key bindings