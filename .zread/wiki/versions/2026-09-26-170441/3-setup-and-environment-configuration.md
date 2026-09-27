This page walks you through every step required to get RubyGemDB running on your machine — from system prerequisites and dependency management to environment variable configuration and installation verification. Whether you are a first-time user setting up the tool for batch classification or a developer preparing the development environment, the instructions below cover both paths with corresponding file references for full transparency.

## Prerequisites — What Your System Needs

RubyGemDB requires **Python 3.14 or newer**, as declared in the project's `.python-version` file and enforced by the `requires-python = ">=3.14"` constraint in `pyproject.toml`. This is a non-negotiable floor: the codebase relies on modern Python features including the improved error messages, exception groups, and typing enhancements introduced in the 3.14 series.

The recommended package manager is **uv** (by Astral), though traditional `pip` installation is supported as a fallback. For the optional AI agent features (semantic vector search and chat), you will also need **Ollama** running locally with the `embeddinggemma` model pulled. The agent uses txtai under the hood, which in turn depends on PyTorch; the project explicitly configures a CPU-only PyTorch index to avoid the heavyweight CUDA dependency.

**Minimum requirements summary:**

| Component | Requirement | Notes |
|-----------|-------------|-------|
| Python | >= 3.14 | Verified via `.python-version` and `pyproject.toml` |
| Package manager | uv (recommended) or pip | uv is 10-100x faster; pip works but slower |
| Ollama + `embeddinggemma` | Only for agent features | `ollama pull embeddinggemma` after installing Ollama |
| Disk space | ~2–3 GB | Mostly from PyTorch CPU wheel and txtai dependencies |
| Internet | Required for first sync | Downloads all packages from PyPI and custom PyTorch index |

Sources: [`.python-version`](.python-version#L1-L2), [`pyproject.toml`](pyproject.toml#L8-L30), [`CLAUDE.md`](CLAUDE.md#L35-L37)

---

## Installation Method 1: uv (Recommended)

The project was designed with **uv** as the primary package manager. The build system uses `uv_build` (declared in `pyproject.toml`) and the lock file `uv.lock` pins every transitive dependency to an exact version, guaranteeing reproducible environments across machines.

```bash
# 1. Clone the repository
git clone https://github.com/rwpannick/rubygemdb.git
cd rubygemdb

# 2. Sync dependencies (includes runtime + dev tools)
uv sync --dev
```

The `uv sync --dev` command reads `pyproject.toml`, resolves all dependencies against `uv.lock`, creates a virtual environment (stored in `.venv/` by default), and installs both runtime packages and dev dependencies (pytest, mypy, ruff). The `--dev` flag installs the `[dependency-groups] dev` group defined at the bottom of `pyproject.toml`.

Behind the scenes, uv applies the custom PyTorch CPU index configured under `[[tool.uv.index]]`:

```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cpu" }
torchvision = { index = "pytorch-cpu" }
torchaudio = { index = "pytorch-cpu" }
```

This ensures that `torch`, `torchvision`, and `torchaudio` are fetched from the CPU-only PyTorch wheel index rather than the default CUDA-enabled index, saving several gigabytes of download.

Sources: [`pyproject.toml`](pyproject.toml#L26-L51), [`INSTALL.md`](INSTALL.md#L4-L10), [`uv.lock`](uv.lock)

---

## Installation Method 2: pip (Alternative)

If you cannot use uv, the standard pip-based workflow works as well, though you lose the lock-file reproducibility:

```bash
git clone https://github.com/rwpannick/rubygemdb.git
cd rubygemdb

# Install in editable mode
pip install -e .

# Install dev dependencies separately
pip install pytest mypy ruff pytest-asyncio pytest-mock
```

Editable mode (`-e .`) installs the `src/rubygemdb` package such that changes to source files are reflected immediately without re-installation. Note that pip will not automatically resolve the PyTorch CPU index — you may need to pre-install torch manually from the CPU wheel if the default pip resolution pulls a CUDA build.

Sources: [`INSTALL.md`](INSTALL.md#L12-L21), [`pyproject.toml`](pyproject.toml#L19-L24)

---

## Centralized Configuration with Pydantic-Settings

All environment configuration flows through a single `Settings` class defined in `src/rubygemdb/core/config.py`. This class inherits from `pydantic_settings.BaseSettings`, which automatically reads values from environment variables and/or a `.env` file, applies default values, and validates types at runtime.

```python
class Settings(BaseSettings):
    mistral_api_key: Optional[str] = None
    context7_api_key: Optional[str] = None
    llm_endpoint: str = "https://api.mistral.ai/v1/chat/completions"
    rubygemdb_model: str = "mistral-medium-latest"
    rubygemdb_agent_model: str = "mistral/mistral-small-latest"
    rate_limit_delay: float = 0.5
    llm_rate_delay: float = 0.5
    llm_batch_size: int = 10
    project_root: Path = Path(__file__).parent.parent.parent.parent
    data_dir: Path = project_root / "data"
    cache_dir: Path = data_dir / "cache"
    gem_cache_file: Path = cache_dir / "gem_cache.json"
    llm_cache_file: Path = cache_dir / "llm_cache.json"
    sqlite_db_file: Path = data_dir / "rubygemdb.sqlite"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
settings.data_dir.mkdir(exist_ok=True)
settings.cache_dir.mkdir(exist_ok=True)
```

**Key design decisions in this module:**

- The module-level singleton `settings = Settings()` is instantiated at import time, then immediately creates the `data/` and `data/cache/` directories if they do not already exist. This means no explicit `mkdir` calls are needed elsewhere in the codebase.
- `env_file=".env"` tells Pydantic to look for a `.env` file relative to the current working directory. The `.env` file is gitignored (see `.gitignore`), which keeps API keys out of version control.
- `extra="ignore"` prevents errors if the `.env` file contains variables not defined on the Settings class, enabling coexistence with other tools' environment variables.
- All path fields resolve relative to `project_root`, which is computed as the grandparent of `config.py` — i.e., the repository root.

Sources: [`src/rubygemdb/core/config.py`](src/rubygemdb/core/config.py#L1-L39), [`.gitignore`](.gitignore#L1-L27)

---

## Environment Variables Reference

The following table documents every environment variable that RubyGemDB recognises, grouped by functional area. Variables with a **bold** name are required for that subsystem to function — without them the relevant service degrades gracefully (e.g., falls back to heuristic-only classification) but will not produce optimal results.

### API & Service Credentials

| Variable | Default | Required For | Notes |
|----------|---------|--------------|-------|
| `MISTRAL_API_KEY` | `None` | LLM classification & agent chat | Obtained from [Mistral AI console](https://console.mistral.ai) |
| `CONTEXT7_API_KEY` | `None` | Context7 documentation search & cheatsheets | Obtained from [Context7 dashboard](https://context7.com) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local embedding generation | Only needed if using Ollama for txtai embeddings |

### LLM Endpoint Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_ENDPOINT` | `https://api.mistral.ai/v1/chat/completions` | API endpoint for classification LLM calls |
| `RUBYGEMDB_MODEL` | `mistral-medium-latest` | Model used for heuristic-fallback classification |
| `RUBYGEMDB_AGENT_MODEL` | `mistral/mistral-small-latest` | Model used for agent semantic chat |
| `TRACKBOI_DISTILLER_MODEL` | `mistral/mistral-large-latest` | Model used for backlog distillation via MCP |

### Rate Limiting & Batching

| Variable | Default | Purpose |
|----------|---------|---------|
| `RATE_LIMIT_DELAY` | `0.5` | Seconds between RubyGems API requests |
| `LLM_RATE_DELAY` | `0.5` | Seconds between LLM API requests |
| `LLM_BATCH_SIZE` | `10` | Number of gems sent per LLM batch request |

### RubyGems API Source

| Variable | Default | Purpose |
|----------|---------|---------|
| `RUBYGEMS_API_URL` | `https://rubygems.org/api/v1/gems/{name}.json` | Template URL for fetching gem metadata; `{name}` is replaced at runtime |

### Storage Paths

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATA_DIR` | `./data` | Root directory for the SQLite database and JSON cache |
| `CACHE_DIR` | `./data/cache` | Subdirectory for API response and LLM caches |

The defaults are derived from the `Settings` class in `config.py`, where `project_root` is the repository root. If you run the tool from a different working directory, either set `PROJECT_ROOT` explicitly or ensure the `.env` file is in your current directory.

Sources: [`src/rubygemdb/core/config.py`](src/rubygemdb/core/config.py#L5-L39), [`INSTALL.md`](INSTALL.md#L168-L200), [`CLAUDE.md`](CLAUDE.md#L35-L49), [`.env`](.env#L1-L12)

---

## Creating the `.env` File

The `.env` file sits at the repository root and is automatically loaded by `pydantic-settings` on every import of `core.config`. Create it by copying the following template:

```bash
# .env — RubyGemDB configuration
# Copy this to the repository root and fill in your keys

# LLM Classification (optional — falls back to heuristic only)
MISTRAL_API_KEY=your_mistral_key_here
LLM_ENDPOINT=https://api.mistral.ai/v1/chat/completions
RUBYGEMDB_MODEL=mistral-medium-latest
RUBYGEMDB_AGENT_MODEL=mistral/mistral-small-latest
LLM_RATE_DELAY=0.5
LLM_BATCH_SIZE=10

# Context7 Documentation (optional — disables cheatsheet features)
CONTEXT7_API_KEY=your_context7_key_here

# RubyGems API
RUBYGEMS_API_URL=https://rubygems.org/api/v1/gems/{name}.json
RATE_LIMIT_DELAY=0.5

# Agent Configuration
RUBYGEMDB_AGENT_MODEL=mistral/mistral-small-latest
TRACKBOI_DISTILLER_MODEL=mistral/mistral-large-latest

# Storage Paths (relative to project root)
DATA_DIR=./data
CACHE_DIR=./data/cache

# Optional: Local Ollama for embeddings
OLLAMA_BASE_URL=http://localhost:11434
```

Because `.env` is listed in `.gitignore`, your keys will never be accidentally committed. The `extra="ignore"` setting on the `Settings` class also means you can safely include variables from other tools (e.g., `OPENAI_API_KEY`) without causing errors.

Source: [`.gitignore`](.gitignore#L1-L27)

---

## torch / PyTorch CPU Index

RubyGemDB depends on PyTorch (`torch`, `torchvision`, `torchaudio`) via txtai's vector similarity and embedding pipeline. The `pyproject.toml` configures an explicit **CPU-only PyTorch index** to avoid pulling the CUDA-enabled wheels, which are ~2 GB larger and unnecessary for text embedding workloads.

```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cpu" }
torchvision = { index = "pytorch-cpu" }
torchaudio = { index = "pytorch-cpu" }
```

If you are using pip, you must pre-install the CPU version manually:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
```

The `explicit = true` flag in the uv config means these packages are fetched **only** from the CPU index, not from the general PyPI pool — a safeguard that prevents accidental CUDA downloads.

Sources: [`pyproject.toml`](pyproject.toml#L40-L51)

---

## Verifying the Installation

Run the following commands to confirm that everything is wired correctly:

```bash
# Check CLI entry point prints help
uv run rubygemdb --help

# Check TUI entry point prints help
uv run rubygemdb-tui --help

# Run the full test suite
uv run pytest

# Run specific tests
uv run pytest tests/test_classifier.py
uv run pytest tests/test_export.py

# Lint check
uv run ruff check src/rubygemdb/

# Type check
uv run mypy src/rubygemdb/
```

The CLI help output confirms that the console scripts defined in `pyproject.toml` (`rubygemdb = "rubygemdb.cli:run_cli"` and `rubygemdb-tui = "rubygemdb.ui.tui:run_tui"`) are correctly registered. The test suite validates the two core areas: heuristic/LLM classification logic and export formatting.

Sources: [`pyproject.toml`](pyproject.toml#L9-L11), [`INSTALL.md`](INSTALL.md#L23-L29), [`tests/test_classifier.py`](tests/test_classifier.py), [`tests/test_export.py`](tests/test_export.py)

---

## File & Directory Layout After Setup

After a successful `uv sync --dev`, your working directory will contain the following structure (focusing on configuration-relevant items):

```
rubygemdb/
├── .env                        # Your environment variables (create manually)
├── .python-version             # Declares Python 3.14
├── pyproject.toml              # Project metadata, dependencies, tool config
├── uv.lock                     # Lock file for reproducible installs
├── .venv/                      # Virtual environment (created by uv)
├── src/rubygemdb/
│   ├── core/
│   │   └── config.py           # Centralized Settings class
│   ├── models/
│   │   └── gem.py              # Pydantic data models
│   ├── services/               # Business logic services
│   ├── storage/                # SQLite + JSON storage layer
│   ├── ui/                     # Textual TUI
│   ├── cli.py                  # CLI entry point
│   └── agent.py                # AI Agent with txtai
├── tests/                      # Test suite
├── data/                       # Created at first import
│   ├── cache/                  # Created at first import
│   └── rubygemdb.sqlite        # Created on first database write
└── output/                     # Created when you run the CLI
```

The `data/` and `data/cache/` directories are created automatically when `config.py` is first imported — you do not need to create them by hand. The SQLite database `rubygemdb.sqlite` is created lazily on the first `INSERT` operation by the storage layer.

Sources: [`src/rubygemdb/core/config.py`](src/rubygemdb/core/config.py#L31-L39), [`.gitignore`](.gitignore#L1-L27)

---

## Troubleshooting Common Setup Issues

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| `RuntimeError: Python 3.14 or higher required` | Wrong Python version | Run `uv python pin 3.14` or install Python 3.14 via pyenv |
| `ModuleNotFoundError: No module named 'torch'` | PyTorch not installed | Run `uv sync --dev` (uv handles it) or `pip install torch --index-url https://download.pytorch.org/whl/cpu` |
| `pydantic_settings` import error | Missing `pydantic-settings` package | Ensure `uv sync` completed without errors; check `.venv` is activated |
| API calls fail silently | Missing API key in `.env` | Verify `MISTRAL_API_KEY` or `CONTEXT7_API_KEY` is set; run `python -c "from rubygemdb.core.config import settings; print(settings.mistral_api_key)"` to debug |
| `sqlite_vec` not found | SQLite extension not loaded | Ensure `sqlite-vec>=0.1.9` is in your dependencies; on some platforms you may need to install system SQLite libs |
| TUI crashes on startup | Missing Textual or Rich | Run `uv sync` again; verify `textual==8.2.3` and `rich>=14.3.3` |

Sources: [`INSTALL.md`](INSTALL.md#L1-L29), [`CLAUDE.md`](CLAUDE.md#L170-L195)

---

## Next Steps

Once the environment is configured and verified, you can proceed through the usage modes in any order depending on your goal:

- If you have a CSV of gem names and want to classify them in bulk, continue to **[CLI Batch Processing Pipeline](4-cli-batch-processing-pipeline)** for a step-by-step walkthrough of the three-phase pipeline (metadata verification → classification → embedding).
- If you prefer to explore and manage gems interactively, proceed to **[TUI Interactive Gem Explorer](5-tui-interactive-gem-explorer)** for the Textual-based terminal interface.
- If you want to query gems using natural language and AI-powered reasoning, skip ahead to **[Agent-Powered Semantic Chat](6-agent-powered-semantic-chat)** after ensuring Ollama is running with the `embeddinggemma` model.

For a deeper understanding of how the configuration system works internally, see **[Centralized Configuration with Pydantic-Settings](9-centralized-configuration-with-pydantic-settings)**, which covers the `Settings` class design, field-by-field semantics, and how `.env` loading works under the hood.