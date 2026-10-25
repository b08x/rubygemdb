# Implementation Plan: Import GitHub Starred RubyGems into RubyGemDB

Import Ruby gems starred by `@b08x` on GitHub into RubyGemDB's inventory database, enrich metadata and inferred GenAI backend tooling roles, audit the resulting catalog for deduplication/staleness, and expose a clean CLI subcommand.

---

## Solution Approach

1. **GitHub Stars Discovery Service**: A new service (`services/github_stars.py`) communicates with the GitHub REST API (authenticating via `GITHUB_TOKEN` / `GH_TOKEN` or `gh auth token` CLI fallback) to retrieve all starred repositories for the user, filtering by primary language (`Ruby`).
2. **Gem Resolution & Verification**: Starred repositories are matched against rubygems.org using `RubyGemsService`. Candidate names are tested via direct repo name, followed by GitHub API inspection of root `*.gemspec` files if the repo name doesn't match directly. Repositories not published as RubyGems are safely logged and skipped.
3. **Database Schema & Models Migration**: Add a `genai_uses` column to `inventory` and `classified_gems` in SQLite (`storage/sqlite_storage.py`), and update `GemInventoryItem` and `GemEntry` Pydantic models (`models/gem.py`).
4. **GenAI Backend Tooling Role Inference**: Add a heuristic inference engine with LLM fallback (`services/classifier.py` / `services/llm.py`) that analyzes each gem's category, description, and capabilities to determine its role in GenAI backend tooling (e.g. prompt tooling, agent memory, tool harness, ETL/preprocessing, evaluation).
5. **Deduplication & Enrichment Pipeline**: Merge newly discovered gems against existing SQLite inventory. For existing gems, backfill missing repository/source metadata and infer missing GenAI uses without overwriting existing data. New valid gems are inserted into the inventory.
6. **Prune & Deduplication Audit**: Run `PruneService` across the catalog to identify stale gems (no release in 3+ years), name-normalization duplicates, and shared-URI monorepos, outputting `data/prune-report.md`. Support `--apply-prune` and `--prune-list` for safety-gated deletion.
7. **CLI Subcommand**: Add `rubygemdb import-stars` to `cli.py` with `--user`, `--language`, `--dry-run`, `--classify`, `--embed`, and `--apply-prune`.

---

## Ordered Steps & Touch Points

### Step 1: Pydantic Data Models & SQLite Schema Migration
- **Files**:
  - `src/rubygemdb/models/gem.py`
  - `src/rubygemdb/storage/sqlite_storage.py`
- **Changes**:
  - Add `genai_uses: Optional[str] = None` to `GemInventoryItem` and `GemEntry`.
  - In `SQLiteStorage._init_db`, add automatic migration for `inventory.genai_uses` and `classified_gems.genai_uses` if columns do not exist.
  - Update `load_inventory`, `save_classified_gems`, `load_classified_gems`, `get_inventory_gem`, `get_all_inventory_gems`, and add `upsert_inventory_gem` / `enrich_inventory_gem` to persist and retrieve `genai_uses`.
- **Verification**:
  - Test SQLite migration on existing `data/rubygemdb.sqlite` and clean in-memory databases.
  - Command: `uv run pytest tests/test_taxonomy_migration.py`

### Step 2: GitHub Starred Repositories Service
- **Files**:
  - `src/rubygemdb/services/github_stars.py` (new)
- **Changes**:
  - Implement `GitHubStarsService` fetching `/users/{user}/starred` with pagination (100 items/page).
  - Support authentication via env `GITHUB_TOKEN`/`GH_TOKEN` or `gh auth token` fallback.
  - Filter repositories where `language == "Ruby"`.
  - Implement gem resolution:
    1. Check if repo name is a valid gem via `RubyGemsService.fetch_gem_info(repo_name)`.
    2. If not found, inspect repository root contents for `*.gemspec` files and test candidate gem names.
    3. Return validated gem dictionary with name, version, source URL, description, homepage, and derived Context7 canonical ID.
- **Verification**:
  - Unit tests mocking GitHub and RubyGems API responses for direct match, gemspec match, and non-gem repos.
  - Command: `uv run pytest tests/test_import_stars.py -k "test_github"`

### Step 3: GenAI Backend Tooling Use Inference
- **Files**:
  - `src/rubygemdb/services/classifier.py`
  - `src/rubygemdb/services/llm.py`
- **Changes**:
  - Implement `infer_genai_tooling_uses(name, description, category, sub_categories)` in classifier.
  - Define structured heuristic rules mapping architectural layers and categories to backend tooling roles (agent harnesses, RAG pipelines, data ingestion, vector memory, prompt engines, structured validation).
  - Connect to `LLMService` when available for refined, context-specific summaries, caching responses via existing `llm_cache.json`.
- **Verification**:
  - Unit tests verifying heuristic inferences across diverse categories (`ai_llm`, `document_parsing`, `cli_libraries`, `persistence`, `runtime_validation`).
  - Command: `uv run pytest tests/test_import_stars.py -k "test_genai_uses"`

### Step 4: Import, Deduplication, and Prune Pipeline
- **Files**:
  - `src/rubygemdb/services/import_stars_pipeline.py` (new or integrated in `cli.py`)
- **Changes**:
  - Deduplicate against existing inventory by gem name and normalized repository URI.
  - Backfill/enrich missing fields (`source_code_uri`, `homepage`, `context7_id`, `genai_uses`) on existing gems without creating duplicate records.
  - Insert new valid gems into inventory.
  - Run `PruneService.build_report()` and write `data/prune-report.md`.
  - If `--apply-prune` or `--prune-list` is passed, prune stale/overlapping gems.
  - In `--dry-run` mode, print detailed summary tables without modifying SQLite or disk files.
- **Verification**:
  - Unit tests verifying deduplication, enrichment, dry-run safety, and prune report generation.
  - Command: `uv run pytest tests/test_import_stars.py -k "test_pipeline"`

### Step 5: CLI Subcommand Integration
- **Files**:
  - `src/rubygemdb/cli.py`
- **Changes**:
  - Register `import-stars` parser with `--user`, `--language`, `--dry-run`, `--classify`, `--embed`, `--apply-prune`, `--prune-list`, and `--out`.
  - Implement `run_import_stars(args)` orchestrating discovery, verification, enrichment, prune audit, and optional classification/embedding.
- **Verification**:
  - Test CLI argument parsing and execution with mock services.
  - Command: `uv run pytest tests/test_cli.py` and `uv run rubygemdb import-stars --help`

### Step 6: Full Suite Verification & Real-World Dry-Run
- **Files**:
  - `tests/test_import_stars.py`
  - Entire test suite
- **Verification**:
  - Run all 221+ existing tests plus new `test_import_stars.py` tests.
  - Execute `uv run rubygemdb import-stars --dry-run` against user's GitHub stars (`b08x`) to verify live execution and report accuracy.
  - Commands:
    - `uv run pytest tests/`
    - `uv run ruff check src/rubygemdb/ tests/`
    - `uv run mypy src/rubygemdb/`
    - `uv run rubygemdb import-stars --dry-run`

---

## Risks & Mitigations

1. **GitHub API Rate Limits**:
   - *Risk*: Fetching 283+ repos unauthenticated can trigger GitHub rate limits (60 req/hr).
   - *Mitigation*: Automatically resolve authenticated tokens via `gh auth token` or `GITHUB_TOKEN` (5,000 req/hr).
2. **RubyGems Rate Limits & Delays**:
   - *Risk*: Querying RubyGems API for dozens of candidate repos.
   - *Mitigation*: Leverage existing `RubyGemsService` cache (`data/cache/gem_cache.json`) and built-in rate-limiting delays.
3. **Accidental Deletions in Pruning**:
   - *Risk*: Pruning shared monorepos or gems by mistake.
   - *Mitigation*: Pruning requires explicit `--apply-prune` or `--prune-list`; `--dry-run` guarantees zero writes.
