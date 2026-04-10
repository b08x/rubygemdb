# AGENTS.md

Guide for AI agents working with the RubyGemDB codebase.

---

## Project Overview

RubyGemDB is a Ruby gem analysis and classification tool that categorizes gems into architectural patterns using heuristic analysis and LLM fallback for ambiguous cases.

### Architectural Categories
8 standardized architectural buckets:
1. `runtime_substrate` - Core Ruby utility libraries
2. `framework_integration` - Rails/Sidekiq ecosystem tools
3. `boundary_interface` - External service connectors
4. `application_capability` - Domain-specific business logic
5. `policy_enforcement` - Auth/authorization tools
6. `observability` - Monitoring/logging tools
7. `developer_experience` - Development/testing tooling
8. `build_delivery` - Build/packaging utilities

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
- `runtime_substrate`: `active_support`, `dry-*`, `core_ext`
- `framework_integration`: Rails/Engine/Sidekiq dependencies
- `boundary_interface`: HTTP clients, AWS/GCP/Stripe SDKs
- `policy_enforcement`: Auth/JWT/Pundit tools
- `observability`: Sentry/Datadog/NewRelic clients
- `developer_experience`: RSpec/Rubocop/Pry/TTY tools

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
    primary: category_name
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