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
| web_frameworks              |    42 |
| document_parsing            |    38 |
| cli_libraries               |    29 |
| background_jobs             |    31 |
```

</td>
<td>

```bash
$ rubygemdb-tui gems-inventory.csv

┌─ RubyGemDB Explorer ─────────────────────────────┐
│ Filter Classification          │ Name  │ Category │
│ ○ All Categories               │ rails │ web_fram │
│ ● web_frameworks               │ pg    │ persiste │
│ ● cli_libraries                │ side..│ cli_lib..│
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
- Ollama with `embeddinggemma` model (for agent features; the MCP server runs FTS5-first without Ollama and loads embeddings lazily only when keyword search returns nothing)
- Optional: local GGUF models for query expansion and cross-encoder reranking — no manual setup; when `QUERY_EXPANSION_ENABLED` or `RERANKER_ENABLED` is set, the models (~1.1 GB and ~640 MB) download lazily from Hugging Face into `data/models/`
- Optional: a Langfuse instance (Langfuse Cloud or self-hosted) for observability — no setup needed to skip it; tracing stays a no-op until `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set

<details>
<summary><strong>uv (Recommended)</strong></summary>

```bash
git clone https://github.com/rwpannick/rubygemdb.git
cd rubygemdb

# Standard installation (CPU)
uv sync --extra cpu

# Optional CUDA installation (NVIDIA GPU acceleration)
uv sync --extra cuda
```

</details>

<details>
<summary><strong>pip</strong></summary>

```bash
git clone https://github.com/rwpannick/rubygemdb.git
cd rubygemdb
pip install -e .
# Dev tooling (pytest, ruff, mypy) is managed by uv's [dependency-groups] via
# `uv sync --dev` — there is no dev-requirements.txt.
```

</details>

<details>
<summary><strong>Verify Installation</strong></summary>

```bash
# Test CLI
uv run rubygemdb --help

# Test TUI
uv run rubygemdb-tui --help

# Test MCP server
uv run rubygemdb-mcp --help

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

1. **Phase 1: Metadata Verification** — Validates gem existence on RubyGems, verifies source URLs, and enriches with Context7 library IDs (canonicalized to `/owner/repo` form, derived from the gem's source/homepage repo URL when possible)
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

Chat sessions persist across restarts: every turn is saved to `data/sessions.sqlite` and indexed for semantic search as a `session_turn` document. The TUI chat tab auto-restores the most recent session on open.

### Embedding Cache Management

Inspect, prune, and clear the shared vector embedding cache (`data/cache/embeddings_cache.sqlite`):

```bash
uv run rubygemdb cache stats                           # Rich table of vector counts, models, and disk footprint
uv run rubygemdb cache clear                           # purge all vectors and reclaim disk space via VACUUM
uv run rubygemdb cache clear --model "model_name"     # clear vectors for a specific model only
uv run rubygemdb cache prune                           # prune expired vectors (>180d) and cap at 100k entries
uv run rubygemdb cache prune --max-age-days 90 --max-entries 50000  # custom retention limits
```

Pass `--no-vacuum` on `clear` or `prune` to skip physical SQLite page reclamation. `uv run rubygemdb reclassify --clear-embeddings-cache` wipes and vacuums the cache during fresh-start reclassification. Retention defaults (TTL 180 days, 100,000 entries) are governed by `EMBEDDINGS_CACHE_TTL_DAYS` / `EMBEDDINGS_CACHE_MAX_ENTRIES` in `.env`.

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

# Storage Paths
PROJECT_ROOT=./
DATA_DIR=./data
CACHE_DIR=./data/cache

# Query Expansion & Cross-Encoder Reranking (optional, off by default)
QUERY_EXPANSION_ENABLED=false
QUERY_EXPANDER_REPO_ID=mradermacher/qmd-query-expansion-1.7B-GGUF
QUERY_EXPANDER_FILENAME=qmd-query-expansion-1.7B.Q4_K_M.gguf
QUERY_EXPANDER_N_CTX=1024
# QUERY_EXPANDER_N_THREADS=      # unset = llama.cpp default
RERANKER_ENABLED=false
RERANKER_REPO_ID=ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF
RERANKER_FILENAME=qwen3-reranker-0.6b-q8_0.gguf
RERANKER_N_CTX=1024
# RERANKER_N_THREADS=           # unset = llama.cpp default
RERANKER_CANDIDATE_MULTIPLIER=3 # candidate pool = top_k × multiplier, clamped 15–50
# MODELS_CACHE_DIR=             # unset = ./data/models

# Embedding Cache Retention (universal vector cache, data/cache/embeddings_cache.sqlite)
# EMBEDDINGS_CACHE_MAX_ENTRIES= # unset = 100000 (oldest vectors pruned above the cap)
# EMBEDDINGS_CACHE_TTL_DAYS=    # unset = 180

# Langfuse Observability (optional - tracing is a no-op until both keys are set)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
# LANGFUSE_HOST=                # unset = https://cloud.langfuse.com (LANGFUSE_BASE_URL also accepted)
# LANGFUSE_ENABLED=             # unset = enabled when keys present; false disables even with keys
```

Legacy aliases (`EXPANDER_*`, `CROSS_ENCODER_*`, `CANDIDATE_MULTIPLIER`, `MODEL_CACHE_DIR`) are also accepted. Both stages fall back to baseline retrieval when the models are disabled, missing, or fail at runtime.

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
- `mcp` >= 1.29.0 — Model Context Protocol server (official SDK; provides `mcp.server.fastmcp`)
- `huggingface_hub` >= 0.28.0 — Hugging Face Hub GGUF model download (query expansion & reranking)
- `llama-cpp-python` >= 0.3.0 — Local GGUF inference for query expansion and cross-encoder reranking
- `langfuse` >= 5.0.4 — Langfuse v5 observability tracing (optional; degrades to a no-op without keys)

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
| `data/sessions.sqlite` | Persistent GraphRAG session store (chat history, citations, traces) |
| `output/{category}.yaml` | Classification reports by category |
| `output/cheatsheets/*.md` | Context7 documentation cheatsheets |
| `data/cache/gem_cache.json` | RubyGems API response cache |
| `data/cache/llm_cache.json` | LLM prompt/response cache |
| `data/cache/embeddings_cache.sqlite` | Universal embedding vector cache (shared across GraphRAG, classification, and sessions; 180-day TTL and 100,000-entry cap with inline pruning after batch writes — manage via `rubygemdb cache {stats, clear, prune}`) |
| `data/txtai/` | Vector embedding index |
| `data/models/` | Local GGUF model cache (query expansion & reranker weights, lazily downloaded from Hugging Face) |
| `data/mcp.pid` | MCP daemon PID file (`data/mcp.json` sidecar records host/port) |
| `data/mcp.log` | MCP daemon log |
