# RubyGemDB

Ruby Gem classification and analysis system implementing a 12-category architectural taxonomy with CLI and TUI interfaces.

## System Overview

RubyGemDB processes Ruby gem metadata through a classification pipeline, storing results in SQLite and providing both batch CLI processing and interactive Textual TUI exploration. The system integrates with RubyGems API for metadata retrieval, LLM services (Devstral/Mistral) for classification refinement, and Context7 for documentation lookup.

### Processing Pipeline

```
CSV Input → Inventory Load → Gem Classification → Storage → Output (YAML/DB)
                    ↑
               RubyGems API
                    ↑
             Heuristic Matching + LLM Verification
```

### Classification Taxonomy

12 primary categories with sub-category detection based on dependency analysis:

- runtime_spine: Rails core, frameworks, Ruby extensions
- cli_terminal_ui: CLI frameworks, terminal UI
- storage_persistence: ORMs, DB adapters, file storage
- async_networking_orchestration: HTTP clients, messaging, job queues, servers
- ai_nlp: AI/ML, NLP, LLM, embeddings
- data_processing: HTML/XML parsing, CSV, spreadsheets, PDF, scraping
- retrieval_similarity_fuzzy: Search, fuzzy matching, indexing
- algorithms_knowledge_structures: Data structures, algorithms, graphs
- validation_types: Validation, type systems, schemas
- parsing_encoding: JSON, YAML, XML, MessagePack, serializers
- debugging_introspection: Debuggers, profilers, loggers, monitoring
- mcp_tooling: Model Context Protocol tools

Sub-categories are detected from dependency names: rails, sidekiq, background_jobs, activejob, server, database, redis, search, auth, api, serialization, spreadsheet, pdf, cloud, monitoring, messaging.

## Installation

### Prerequisites

- Python >= 3.14
- uv (recommended) or pip

### From Source

```bash
git clone https://github.com/rwpannick/rubygemdb.git
cd rubygemdb
python -m venv .venv
source .venv/bin/activate
uv sync
```

### Development

```bash
uv sync --all-extras
```

## Configuration

Environment variables are loaded via `.env` file or system environment using Pydantic Settings:

```bash
# LLM Classification (optional, falls back to heuristic only)
DEVSTRAL_API_KEY=string
LLM_ENDPOINT=https://api.mistral.ai/v1/chat/completions
LLM_MODEL=devstral-small
LLM_RATE_DELAY=0.5
LLM_BATCH_SIZE=10

# Context7 Documentation (optional)
CONTEXT7_API_KEY=string

# RubyGems API (default: https://rubygems.org/api/v1/gems/{name}.json)
RUBYGEMS_API_URL=string

# Storage
PROJECT_ROOT=./
DATA_DIR=./data
CACHE_DIR=./data/cache
```

## Data Models

### GemEntry (Pydantic)

Primary data structure representing a classified gem:

```python
class GemEntry(BaseModel):
    name: str
    classification: GemClassification
    role: Dict[str, str]
    capabilities: List[str]
    risks: GemRisks
    signals: GemSignals
    dependencies: List[str]
    description: Optional[str]
    homepage: Optional[str]
    source_code_uri: Optional[str]
    context7_id: Optional[str]
```

### GemClassification (Pydantic)

```python
class GemClassification(BaseModel):
    primary: str              # One of 12 categories
    secondary: Optional[str]
    sub_categories: List[str]  # Detected from dependencies
    confidence: float          # 0.0 - 1.0
```

### GemRisks (Pydantic)

Risk assessment metrics:

```python
class GemRisks(BaseModel):
    invasiveness: int    # 1-5 scale
    coupling: int        # 1-4 scale
    abstraction_leak: str # "low", "medium", "high"
```

### GemSignals (Pydantic)

Boolean flags for gem characteristics:

```python
class GemSignals(BaseModel):
    rails: bool = False
    external_io: bool = False
    native_ext: bool = False
```

### GemInventoryItem (Pydantic)

Input data structure from CSV:

```python
class GemInventoryItem(BaseModel):
    name: str
    version: Optional[str]
    category: Optional[str]
    description: Optional[str]
    homepage: Optional[str]
    source_code_uri: Optional[str]
    context7_id: Optional[str]
```

## Classification Algorithm

### Stage 1: Heuristic Matching

Pattern-based classification with confidence scoring:

1. Name pattern matching against category keywords
2. Dependency-based category inference
3. Confidence accumulation (base: 0.5, +0.3 per match)
4. Sub-category extraction from dependency names

Pattern mappings:

```python
# Category → Name patterns
runtime_spine: ["rails", "engine", "railties", "active_support", "core_ext", "bundler", "dry-"]
cli_terminal_ui: ["thor", "gli", "tty-", "commander", "optimist", "clamp", "cli", "terminal", "curses", "ncurses"]
storage_persistence: ["activerecord", "sequel", "rom", "mongoid", "ohm", "redis-store", "leveldb", "lmdb", "sqlite", "ar-"]
async_networking_orchestration: ["sidekiq", "async", "falcon", "puma", "unicorn", "resque", "delayed_job", "http", "faraday", "net-http", "excon", "typhoeus", "patron", "grpc", "kafka", "bunny", "mqtt", "celluloid", "concurrent", "eventmachine", "nio4r"]
ai_nlp: ["openai", "ruby-openai", "anthropic", "llm", "gpt", "nlp", "langchain", "transformers", "embedding", "vector", "tiktoken", "tokenizer", "tensorflow", "torch", "onnx", "whisper"]
data_processing: ["nokogiri", "oga", "loofah", "sanitiz", "roo", "spreadsheet", "caxlsx", "axlsx", "xlsx", "csv", "prawn", "wicked_pdf", "pdfkit", "hexapdf", "mechanize", "scraping", "scraper", "craw"]
retrieval_similarity_fuzzy: ["elasticsearch", "searchkick", "chewy", "meilisearch", "fuzzy", "fuzz", "similar", "match", "pg_search", "ransack", "sunspot", "thinking-sphinx"]
algorithms_knowledge_structures: ["algorithm", "rbtree", "tree", "graph", "heap", "queue", "set-theory", "bitset", "bloom", "trie", "hash_ring", "priority-queue", "linked-list"]
validation_types: ["dry-validation", "dry-types", "dry-struct", "dry-schema", "validates", "valid_attr", "attribute", "json-schema", "json_schemer", "activemodel"]
parsing_encoding: ["json", "yajl", "oj", "msgpack", "yaml", "toml", "xml", "serializ", "encode", "decode", "marshal", "protobuf"]
debugging_introspection: ["pry", "byebug", "debug", "debase", "ruby-debug", "stackprof", "ruby-prof", "memory_profiler", "allocation_tracer", "log", "logger", "sentry", "datadog", "newrelic", "honeybadger", "bugsnag", "airbrake", "rollbar", "opentelemetry"]
mcp_tooling: ["mcp", "model-context-protocol"]
```

### Stage 2: LLM Verification

When heuristic confidence < 0.7, LLM is queried with structured prompt:

```
Prompt Template:
"Classify this Ruby gem into ONE category:
- runtime_spine
- cli_terminal_ui
- storage_persistence
- async_networking_orchestration
- ai_nlp
- data_processing
- retrieval_similarity_fuzzy
- algorithms_knowledge_structures
- validation_types
- parsing_encoding
- debugging_introspection
- mcp_tooling

Gem: {name}
Description: {description}
Dependencies: {dependencies}

Return JSON only:
{{"primary":"...","confidence":0.0}}"
```

Response parsing handles markdown code blocks and extracts JSON. Results are cached using SHA256 hash of prompt.

### Risk Scoring

Invasiveness mapping:

```python
{
    "runtime_spine": 5,
    "async_networking_orchestration": 4,
    "storage_persistence": 4,
    "data_processing": 3,
    "ai_nlp": 3,
    "retrieval_similarity_fuzzy": 3,
    "algorithms_knowledge_structures": 2,
    "validation_types": 2,
    "parsing_encoding": 2,
    "cli_terminal_ui": 1,
    "debugging_introspection": 1,
    "mcp_tooling": 1,
}
```

Coupling: `min(4, max(1, len(dependencies)//3 + 1))`

Abstraction leak mapping:

```python
{
    "async_networking_orchestration": "high",
    "storage_persistence": "high",
    "runtime_spine": "medium",
    "ai_nlp": "medium",
    "data_processing": "medium",
    "default": "low"
}
```

## Storage Layer

### SQLite Schema

```sql
-- Inventory table: raw input data
CREATE TABLE IF NOT EXISTS inventory (
    name TEXT PRIMARY KEY,
    version TEXT,
    category TEXT,
    description TEXT,
    homepage TEXT,
    source_code_uri TEXT,
    context7_id TEXT,
    verified INTEGER DEFAULT 0
);

-- Classified gems table: processed results
CREATE TABLE IF NOT EXISTS classified_gems (
    name TEXT PRIMARY KEY,
    classification TEXT,        -- JSON: GemClassification
    role TEXT,                  -- JSON: Dict[str, str]
    capabilities TEXT,          -- JSON: List[str]
    risks TEXT,                 -- JSON: GemRisks
    signals TEXT,               -- JSON: GemSignals
    dependencies TEXT,          -- JSON: List[str]
    description TEXT,
    FOREIGN KEY(name) REFERENCES inventory(name)
);
```

### Caching

Three cache files:

1. `data/cache/gem_cache.json` - RubyGems API responses (key: gem name)
2. `data/cache/llm_cache.json` - LLM responses (key: SHA256 hash of prompt)
3. SQLite database - Persistent storage of inventory and classifications

## CLI Interface

### Entry Point

`rubygemdb.cli:run_cli` via pyproject.toml scripts.

### Command Structure

```bash
rubygemdb <csv_path> [--out <output_dir>]
```

### Processing Flow

1. Parse arguments (csv path, output directory)
2. Create service instances:
   - RubyGemsService()
   - LLMService()
   - Context7Service()
   - GemClassifier(rubygems_service, llm_service)
   - SQLiteStorage(rubygems_service, context7_service)
3. Load inventory from CSV via SQLiteStorage.load_inventory()
4. For each gem:
   a. Classify via GemClassifier.classify()
   b. Append to results list
   c. Group by primary category
5. Save to SQLite via SQLiteStorage.save_classified_gems()
6. Write YAML files per category to output directory
7. Display summary table via Rich Table

### YAML Output Format

```yaml
# Filename: output/{category}.yaml
- name: rails
  classification:
    primary: runtime_spine
    secondary: null
    sub_categories: []
    confidence: 0.95
  role: {}
  capabilities: []
  risks:
    invasiveness: 5
    coupling: 1
    abstraction_leak: medium
  signals:
    rails: true
    external_io: false
    native_ext: false
  dependencies: [activesupport, actionpack, ...]
  description: Web application framework
  homepage: https://rubyonrails.org
  source_code_uri: https://github.com/rails/rails
  context7_id: /rails/rails
```

## TUI Interface

### Entry Point

`rubygemdb.ui.tui:run_tui` via pyproject.toml scripts.

### Architecture

Textual App with:
- Header (title + clock)
- Sidebar (classification filter RadioSet)
- Main area (TabbedContent with Explorer, Export, Debug tabs)
- Details sidebar (GemDetails widget)
- Footer (key bindings)

### Widget Hierarchy

```
GemApp (App)
├── Header
├── Horizontal
│   ├── Vertical (sidebar)
│   │   └── RadioSet (class_filter, 13 buttons: All + 12 categories)
│   └── Vertical (main-area)
│       └── TabbedContent (main-tabs)
│           ├── TabPane (explorer)
│           │   └── DataTable (gems_table)
│           ├── TabPane (export)
│           │   └── ExportTab (ScrollableContainer)
│           │       ├── Label (export-summary)
│           │       ├── RadioSet (export-format: gemfile/csv/json/md)
│           │       ├── Markdown (export-preview)
│           │       ├── Input (export-path-input)
│           │       └── Button (do-export-btn)
│           └── TabPane (debug)
│               └── RichLog (debug-log)
└── GemDetails (Vertical, display: none initially)
    ├── ScrollableContainer
    │   ├── Label (details-name)
    │   ├── Label (details-classification)
    │   ├── Label (details-metadata)
    │   ├── Label (details-risks)
    │   ├── Markdown (details-description)
    │   ├── Label (Runtime Dependencies)
    │   ├── ListView (details-deps-list)
    │   ├── Horizontal (action-buttons)
    │   │   ├── Button (fetch-cheatsheet-btn)
    │   │   └── Button (lookup-c7-btn)
    │   └── Button (close-details-btn)
    └── Button (fetch-combined-btn)
└── Footer
```

### Key Bindings

```python
BINDINGS = [
    ("q", "quit", "Quit"),
    ("ctrl+q", "quit", "Quit"),
    ("ctrl+c", "quit", "Quit"),
    ("escape", "quit", "Quit"),
    ("r", "refresh", "Refresh"),
    ("space", "toggle_selection", "Toggle"),
    ("a", "select_all", "Select All"),
    ("n", "clear_selection", "Clear"),
    ("u", "bulk_update_context7", "Update C7"),
    ("e", "export_selected", "Export"),
    ("t", "switch_tab", "Switch Tab"),
]
```

### Data Loading

Asynchronous via `@work(thread=True)` decorator:

1. SQLiteStorage.load_inventory() if CSV path provided
2. SQLiteStorage.load_classified_gems() from DB
3. Populate DataTable with filtered results
4. Update table on classification filter change

### Export Formats

Four export methods in ExportTab:

**Gemfile:**
```ruby
source 'https://rubygems.org'
gem 'rails'
gem 'pg'
```

**CSV:**
```csv
Name,Category,Source URI,Context7 ID,Description
rails,runtime_spine,https://...,/rails/rails,Web application framework
```

**JSON:**
```json
[{
  "name": "rails",
  "classification": {"primary": "runtime_spine", ...},
  "dependencies": [...],
  ...
}]
```

**Markdown:**
```markdown
| Name | Category | Source | Context7 ID | Description |
|------|----------|--------|-------------|-------------|
| rails | runtime_spine | https://... | /rails/rails | Web application framework |
```

### Cheatsheet Generation

Context7 integration for documentation:

1. Context7Service.verify_library(gem_name) → library_id
2. Context7Service.query_context(lib_id, query) → documentation text
3. Write to `output/cheatsheets/{gem_name}_cheatsheet.md`

Bulk operation: fetch_combined_cheatsheet() generates single file with all selected gems.

## Service Layer

### RubyGemsService

RubyGems API client with caching:

```python
class RubyGemsService:
    cache: Dict[str, dict]  # gem name → API response
    
    def fetch_gem_info(name: str, retries: int = 3) -> Optional[dict]:
        # Returns: {"name": str, "version": str, "info": str, "homepage_uri": str, 
        #           "source_code_uri": str, "dependencies": {"runtime": [...]}, ...}
```

### LLMService

LLM client with prompt caching:

```python
class LLMService:
    cache: Dict[str, dict]  # SHA256(prompt) → response
    
    def build_prompt(name: str, info: dict, deps: list) -> str:
        # Returns classification prompt with category list
    
    def call_llm(prompt: str) -> Optional[dict]:
        # POST to LLM_ENDPOINT, returns {"primary": str, "confidence": float}
```

### Context7Service

Context7 API client:

```python
class Context7Service:
    api_key: str
    
    def search_libraries(gem_name: str) -> List[dict]:
        # GET /api/v2/libs/search, returns library matches
    
    def verify_library(gem_name: str) -> Optional[str]:
        # Returns library_id or None
    
    def query_context(lib_id: str, query: str) -> Optional[str]:
        # GET /api/v2/context, returns documentation text
```

## Input/Output

### Input CSV Format

Headers (minimum `name` required):

```csv
name,version,category,description,homepage,source_code_uri,context7_id
```

Example:

```csv
gem,version,category
gem1,1.0.0,framework
gem2,2.0.0,library
```

### Output Files

1. SQLite database: `data/rubygemdb.sqlite`
2. YAML files: `output/{category}.yaml` (one per category)
3. Cheatsheets: `output/cheatsheets/*.md`

## Testing

### Test Files

- `tests/test_classifier.py` - Classification logic tests
- `tests/test_export.py` - Export format tests

### Running Tests

```bash
pytest
pytest tests/test_classifier.py
pytest --cov=src/rubygemdb
```

### Test Coverage

- Classification heuristics (12 categories)
- Sub-category detection
- Risk scoring
- Export format generation (Gemfile, CSV, JSON, Markdown)
- LLM prompt building

## Project Structure

```
rubygemdb/
├── src/
│   └── rubygemdb/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── core/
│       │   └── config.py
│       ├── models/
│       │   └── gem.py
│       ├── services/
│       │   ├── classifier.py
│       │   ├── context7.py
│       │   ├── llm.py
│       │   └── rubygems.py
│       ├── storage/
│       │   ├── base.py
│       │   ├── json_storage.py
│       │   └── sqlite_storage.py
│       └── ui/
│           └── tui.py
├── data/
│   ├── cache/
│   │   ├── gem_cache.json
│   │   └── llm_cache.json
│   ├── classified_gems.json
│   └── rubygemdb.sqlite
├── output/
│   ├── *.yaml
│   └── cheatsheets/
│       └── *.md
├── tests/
│   ├── test_classifier.py
│   └── test_export.py
├── pyproject.toml
└── README.md
```

## Dependencies

Runtime (pyproject.toml):

```toml
[project.dependencies]
pydantic = ">=2.12.5"
pydantic-settings = ">=2.13.1"
python-dotenv = ">=1.2.2"
pyyaml = ">=6.0.3"
requests = ">=2.33.1"
rich = ">=14.3.3"
textual = "==8.2.3"
```

Development:

```toml
[dependency-groups.dev]
mypy = ">=1.20.0"
pytest = ">=9.0.3"
pytest-asyncio = ">=1.3.0"
pytest-mock = ">=3.15.1"
ruff = ">=0.15.10"
```

## License

MIT License. See LICENSE file for full text.

## References

- RubyGems API: https://rubygems.org/api/v1/gems/{name}.json
- Context7 API: https://context7.com/api/v2/
- Textual: https://textual.textualize.io/
- Pydantic: https://pydantic.dev/
