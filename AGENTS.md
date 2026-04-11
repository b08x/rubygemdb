# AGENTS.md

Guide for AI agents working with the RubyGemDB codebase.

---

## Project Overview

RubyGemDB is a Ruby gem analysis and classification tool that categorizes gems into architectural patterns using heuristic analysis and LLM fallback for ambiguous cases.

### Architectural Categories
12 standardized architectural categories:
1. `runtime_spine` — Boot + Wiring (Rails core, frameworks, core Ruby exts)
2. `cli_terminal_ui` — CLI & Terminal UI Layer
3. `storage_persistence` — Storage & Persistence (ORMs, DB adapters)
4. `async_networking_orchestration` — Async, Networking & Orchestration
5. `ai_nlp` — AI / NLP Layer
6. `data_processing` — Data Processing (HTML/XML, CSV, PDF, scraping)
7. `retrieval_similarity_fuzzy` — Retrieval, Similarity & Fuzzy Matching
8. `algorithms_knowledge_structures` — Algorithms / Knowledge Structures
9. `validation_types` — Validation & Types
10. `parsing_encoding` — Parsing / Encoding Boundaries
11. `debugging_introspection` — Debugging & Introspection
12. `mcp_tooling` — MCP Tooling

---

## Architecture

### Data Flow
1. Read gem inventory from CSV → Fetch RubyGems API data → Heuristic classification → LLM enhancement (optional) → Save results to YAML/SQLite
2. Caching layers to minimize redundant API/LLM calls
3. Persistent storage for classified gem results

### Core Component Directory Structure
```
src/rubygemdb/
├── cli.py              # Command-line interface batch processor
├── ui/
│   └── tui.py          # Interactive Textual TUI explorer
├── core/
│   └── config.py       # Settings and environment variable handling
├── models/
│   └── gem.py          # Pydantic data models for gem data
├── services/
│   ├── classifier.py   # Heuristic and LLM classification logic
│   ├── rubygems.py     # RubyGems API client with caching
│   ├── llm.py          # Devstral LLM client for fallback classification
│   └── context7.py     # Context7 API client for cheatsheet generation
└── storage/
    ├── base.py         # Storage interface
    ├── json_storage.py # JSON file storage implementation
    └── sqlite_storage.py # SQLite storage implementation
```

---

## Key Commands

### Install Dependencies
```bash
# Using uv (recommended)
uv sync --dev

# Or pip
pip install -e .
pip install -r dev-requirements.txt
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
# Devstral API key for LLM fallback classification
export DEVSTRAL_API_KEY="your_devstral_key"

# Context7 API key for generating gem usage cheatsheets
export CONTEXT7_API_KEY="your_context7_key"
```

---

## Classification Logic

### Heuristic Classification
First-pass classification based on gem name patterns and dependencies:
- `runtime_spine`: `rails`, `active_support`, `bundler`, `dry-*` (generic)
- `cli_terminal_ui`: `thor`, `gli`, `tty-*`, `commander`, `clamp`
- `storage_persistence`: `activerecord`, `sequel`, `mongoid`, `sqlite`
- `async_networking_orchestration`: `sidekiq`, `async`, `faraday`, `grpc`, `kafka`
- `ai_nlp`: `openai`, `ruby-openai`, `llm`, `nlp`, `langchain`
- `data_processing`: `nokogiri`, `roo`, `prawn`, `mechanize`
- `retrieval_similarity_fuzzy`: `elasticsearch`, `searchkick`, `fuzzy`
- `algorithms_knowledge_structures`: `algorithm`, `rbtree`, `graph`, `trie`
- `validation_types`: `dry-validation`, `dry-types`, `activemodel`, `json-schema`
- `parsing_encoding`: `json`, `yajl`, `oj`, `msgpack`, `xml`
- `debugging_introspection`: `pry`, `byebug`, `sentry`, `datadog`, `newrelic`
- `mcp_tooling`: `mcp`, `model-context-protocol`

### LLM Fallback
Triggers when heuristic confidence < 0.7: Uses Devstral API to refine classification with 0.6 minimum confidence threshold to override heuristics.

---

## Caching Strategy

Local cache files:
1. `gem_cache.json`: Raw RubyGems API responses
2. `llm_cache.json`: SHA256-hashed LLM prompt/response pairs
3. `classified_gems.json`: Persisted TUI classification results
4. `data/rubygemdb.sqlite`: Central SQLite database for inventory and classified gems

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
- `context7_id`: Context7 library ID for cheatsheet generation

### Output YAML
Per-category YAML files contain structured gem classification data:
```yaml
- name: gem_name
  classification:
    primary: category_name  # one of the 12 new slugs
    confidence: 0.85
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
3. **Rate Limiting**: Built-in delays between API requests: 0.1s between RubyGems calls, 0.2s between LLM calls
4. **Batch Size**: Default LLM batch size is 10 for efficient bulk processing
5. **TUI Requirements**: CSV input only required for first run to populate SQLite database

---

## Testing

While no formal test suite exists yet, the standard workflow would be:
1. Run unit tests for individual services: `uv run pytest tests/`
2. Validate classification logic with sample gems: `uv run python -m rubygemdb data/sample-gems.csv --out test-output/`

## Existing Documentation

Additional context available in:
- `CLAUDE.md`: Legacy Claude-specific guidance
- `GEMINI.md`: Original project documentation