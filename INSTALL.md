# Installation & Usage

## Quick Start

```bash
# Install
uv sync --dev

# Classify gems
uv run rubygemdb gems-inventory.csv --out output/

# Launch TUI
uv run rubygemdb-tui gems-inventory.csv
```

## Demo

<table>
<tr>
<td><h4>CLI Pipeline</h4></td>
<td><h4>TUI Explorer</h4></td>
</tr>
<tr>
<td>

```bash
$ rubygemdb gems-inventory.csv --out output/

--- PHASE 1: Metadata Verification (247 gems) ---
Verifying rails... ✓
Verifying nokogiri... ✓

--- PHASE 2: Heuristic & LLM Classification ---
Classifying gems... ━━━━━━━━━━━━━━━━━━━━━━━━ 100%

| Category                    | Count |
|-----------------------------|-------|
| runtime_spine               |    42 |
| data_processing             |    38 |
| cli_terminal_ui             |    29 |
| async_networking            |    31 |
```

</td>
<td>

```bash
$ rubygemdb-tui gems-inventory.csv

┌─ RubyGemDB Explorer ─────────────────────────────┐
│ Filter Classification          │ Name  │ Category │
│ ○ All Categories               │ rails │ runtime_ │
│ ● runtime_spine                │ pg    │ storage_ │
│ ● cli_terminal_ui              │ side..│ async_n..│
│ ...                           │       │          │
└────────────────────────────────┴───────┴──────────┘
```

</td>
</tr>
</table>

---

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Ollama with `embeddinggemma` model (for agent features)

<details>
<summary><strong>uv (Recommended)</strong></summary>

```bash
git clone https://github.com/rwpannick/rubygemdb.git
cd rubygemdb
uv sync --dev
```

</details>

<details>
<summary><strong>pip</strong></summary>

```bash
git clone https://github.com/rwpannick/rubygemdb.git
cd rubygemdb
pip install -e .
pip install -r dev-requirements.txt
```

</details>

<details>
<summary><strong>Verify Installation</strong></summary>

```bash
# Test CLI
uv run rubygemdb --help

# Test TUI
uv run rubygemdb-tui --help

# Run tests
uv run pytest
```

</details>

---

## Usage

### CLI: Batch Classification

Process a CSV inventory of gem names and generate YAML classification reports:

```bash
# Full pipeline with CSV input
uv run rubygemdb gems-inventory.csv --out output/

# Skip Phase 1 verification (use existing DB data)
uv run rubygemdb gems-inventory.csv --out output/ --skip-verify

# Generate vector embeddings after classification
uv run rubygemdb gems-inventory.csv --out output/ --embed
```

**Processing Phases:**

1. **Phase 1: Metadata Verification** — Validates gem existence on RubyGems, verifies source URLs, and enriches with Context7 library IDs
2. **Phase 2: Classification** — Applies heuristic matching with LLM fallback for low-confidence cases
3. **Phase 3: Embeddings** — Builds txtai vector index for semantic search (optional)

### TUI: Interactive Explorer

Launch the terminal UI for browsing and managing classified gems:

```bash
# With initial CSV (first run only)
uv run rubygemdb-tui gems-inventory.csv

# From existing database
uv run rubygemdb-tui
```

### Agent: Semantic Search & AI Chat

Query the embedded agent for gem recommendations and implementation plans:

```python
from rubygemdb.agent import TxtaiAgent

agent = TxtaiAgent()
response = agent.run("Find gems for building a REST API with rate limiting")
print(response)
```

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Refresh data |
| `c` | Open agent chat |
| `e` | Edit selected gem |
| `d` | Delete selected gem |
| `+` | Add new gem |
| `space` | Toggle gem selection |
| `a` | Select all gems |
| `n` | Clear selection |
| `u` | Bulk update Context7 IDs |
| `x` | Export selected gems |
| `t` | Switch tab |

---

## Configuration

Environment variables loaded via `.env` file or system environment using Pydantic Settings:

```bash
# LLM Classification (optional - falls back to heuristic only)
MISTRAL_API_KEY=your_mistral_key
LLM_ENDPOINT=https://api.mistral.ai/v1/chat/completions
RUBYGEMDB_MODEL=mistral-medium-latest
RUBYGEMDB_AGENT_MODEL=mistral/mistral-small-latest
LLM_RATE_DELAY=0.5
LLM_BATCH_SIZE=10

# Context7 Documentation (optional)
CONTEXT7_API_KEY=your_context7_key

# RubyGems API
RUBYGEMS_API_URL=https://rubygems.org/api/v1/gems/{name}.json
RATE_LIMIT_DELAY=0.5

# Agent Configuration
RUBYGEMDB_AGENT_MODEL=mistral/mistral-small-latest
TRACKBOI_DISTILLER_MODEL=mistral/mistral-large-latest

# Storage Paths
PROJECT_ROOT=./
DATA_DIR=./data
CACHE_DIR=./data/cache
```

---

## Running Tests

```bash
# All tests
uv run pytest

# Specific test file
uv run pytest tests/test_classifier.py

# With coverage
uv run pytest --cov=src/rubygemdb
```

## Linting & Type Checking

```bash
# Ruff linter
uv run ruff check src/rubygemdb/

# Mypy type checker
uv run mypy src/rubygemdb/
```

---

## Dependencies

### Runtime

- `pydantic` >= 2.12.5 — Data validation and settings
- `pydantic-settings` >= 2.13.1 — Environment variable management
- `requests` >= 2.33.1 — HTTP client
- `rich` >= 14.3.3 — Terminal formatting
- `textual` == 8.2.3 — TUI framework
- `txtai[agent,pipeline,similarity]` >= 9.13.0 — Vector embeddings
- `sqlite-vec` >= 0.1.9 — SQLite vector extension
- `pyyaml` >= 6.0.3 — YAML serialization

### Development

- `pytest` >= 9.0.3
- `ruff` >= 0.15.10
- `mypy` >= 1.20.0

---

## Contributing

Contributions welcome. Run `uv run ruff check` and `uv run mypy` before submitting.

---

## Input/Output

### CSV Input Format

```csv
name,version,category,description,homepage,source_code_uri,context7_id
rails,7.1.0,framework,Ruby on Rails,...
nokogiri,1.15.0,,HTML/XML parser,...
```

**Required columns:** `name` or `gem`

### Output Files

| File | Description |
|------|-------------|
| `data/rubygemdb.sqlite` | Primary SQLite database |
| `output/{category}.yaml` | Classification reports by category |
| `output/cheatsheets/*.md` | Context7 documentation cheatsheets |
| `data/cache/gem_cache.json` | RubyGems API response cache |
| `data/cache/llm_cache.json` | LLM prompt/response cache |
| `data/txtai/` | Vector embedding index |
