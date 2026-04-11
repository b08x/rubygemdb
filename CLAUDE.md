# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RubyGemDB is a Ruby gem analysis and classification tool that categorizes gems into architectural patterns using heuristics and LLM-based analysis. The system provides both command-line and interactive terminal UI (TUI) interfaces for exploring and understanding Ruby gem ecosystems.

## Architecture

### Core Classification System
- **12 Architectural Categories**: runtime_spine, cli_terminal_ui, storage_persistence, async_networking_orchestration, ai_nlp, data_processing, retrieval_similarity_fuzzy, algorithms_knowledge_structures, validation_types, parsing_encoding, debugging_introspection, mcp_tooling
- **Heuristic Analysis**: Pattern-based classification using gem names, dependencies, and metadata
- **LLM Fallback**: Devstral LLM integration for ambiguous cases (confidence < 0.7)
- **Confidence Scoring**: Risk assessment based on invasiveness, coupling, and abstraction leak potential

### Data Flow
1. CSV input → RubyGems API → Heuristic classification → LLM enhancement → SQLite/YAML output
2. Caching layers for both API responses (`data/cache/gem_cache.json`) and LLM responses (`llm_cache.json`)
3. Results cached in `data/classified_gems.json` and SQLite database for TUI persistence

### Key Components
```
src/rubygemdb/
├── cli.py              # Command-line interface entry point
├── ui/tui.py          # Interactive Textual-based TUI with gem explorer
├── core/config.py     # Settings and environment variable handling  
├── models/gem.py      # Pydantic data models for gem data
├── services/          # Core business logic
│   ├── classifier.py  # Heuristic and LLM classification logic
│   ├── rubygems.py    # RubyGems API client with caching
│   ├── llm.py         # Devstral LLM client for fallback classification
│   └── context7.py    # Context7 API client for cheatsheet generation
└── storage/           # Data persistence layer
    ├── base.py        # Storage interface
    ├── json_storage.py# JSON file storage implementation
    └── sqlite_storage.py # SQLite storage implementation (primary)
```

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

### Heuristic Patterns
- **Runtime Spine**: Boot + wiring (rails, active_support, bundler, dry-*)
- **CLI/Terminal UI**: CLI frameworks (thor, gli, tty-*, commander, clamp)
- **Storage/Persistence**: ORMs, DB adapters (activerecord, sequel, mongoid, sqlite)
- **Async/Networking**: HTTP, messaging, jobs (sidekiq, async, faraday, grpc, kafka)
- **AI/NLP**: AI/ML, LLMs (openai, ruby-openai, llm, nlp, langchain)
- **Data Processing**: Parsing, scraping (nokogiri, roo, prawn, mechanize)
- **Retrieval/Similarity**: Search, fuzzy (elasticsearch, searchkick, fuzzy)
- **Algorithms/Knowledge**: Data structures (algorithm, rbtree, graph, trie)
- **Validation/Types**: Validation, type systems (dry-validation, dry-types, activemodel)
- **Parsing/Encoding**: Serializers (json, yajl, oj, msgpack, xml)
- **Debugging/Introspection**: Debuggers, loggers, profilers (pry, byebug, sentry, datadog)
- **MCP Tooling**: Model Context Protocol tools (mcp)

### Confidence Thresholds
- High confidence (>0.7): Use heuristic classification
- Low confidence (<0.7): Queue for LLM batch processing
- LLM confidence threshold: 0.6 to override heuristics

## Input Data Format

CSV files must contain either:
- `name` column (primary) or `gem` column  
- Optional columns: `group`, `category`, `description`, `homepage`, `source_code_uri`, `context7_id`

## Storage and Caching Strategy

### Primary Storage
- **SQLite Database**: `data/rubygemdb.sqlite` - Central storage for inventory and classified gems
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
    secondary: null
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