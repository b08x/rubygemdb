# Implementation Plan: Universal Embedding Cache Lifecycle, Retention, and Configuration

## Solution Approach
Resolve the dead configuration defect and unbounded storage growth in RubyGemDB's universal vector embedding cache (`embeddings_cache.sqlite`) through a three-stage architectural overhaul:
1. **Config Binding & Disconnect Rectification**:
   - Update `EmbeddingCache.__init__` to default `db_path` directly to `settings.embeddings_cache_file` from `core/config.py`.
   - Add retention configuration attributes to `Settings`: `embeddings_cache_max_entries: int = 100_000` and `embeddings_cache_ttl_days: int = 180` with environment variable mappings (`EMBEDDINGS_CACHE_MAX_ENTRIES`, `EMBEDDINGS_CACHE_TTL_DAYS`).
2. **Lifecycle, Pruning & Disk Reclamation**:
   - Enhance `EmbeddingCache.clear()` with a `vacuum: bool = True` parameter executing `VACUUM` on SQLite connection to immediately reclaim OS disk space.
   - Implement `EmbeddingCache.prune(max_age_days, max_entries, vacuum)` to purge expired vectors based on `created_at` and trim oldest entries when exceeding capacity, automatically vacuuming if >10% of entries are pruned.
   - Add opportunistic retention checks during `put_many` batch writes when table entry counts exceed configured thresholds.
   - Enhance `EmbeddingCache.stats()` to report physical file footprint (DB file, WAL, SHM, and total disk bytes).
3. **Operator CLI Surface & Reclassification Integration**:
   - Introduce dedicated `rubygemdb cache {stats, clear, prune}` CLI command group with formatted Rich table outputs.
   - Add `--clear-embeddings-cache` flag to `rubygemdb reclassify` allowing operators to purge and vacuum vectors during fresh-start taxonomical rebuilds.
4. **Documentation & Comprehensive Verification**:
   - Update `AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, and `README.md` to remove the growth caveat and document the CLI commands and retention settings.
   - Add automated test coverage for config resolution, VACUUM execution, prune logic, inline retention, and CLI commands.

---

## Ordered Implementation Steps

### Step 1: Update Settings Configuration in `src/rubygemdb/core/config.py`
- **Files**: `src/rubygemdb/core/config.py`
- **Actions**:
  - Add `embeddings_cache_max_entries: int = 100_000` to `Settings`.
  - Add `embeddings_cache_ttl_days: int = 180` to `Settings`.
  - Ensure `embeddings_cache_file` remains defined as `cache_dir / "embeddings_cache.sqlite"`.
- **Verification**:
  - Run `uv run python -c "from rubygemdb.core.config import settings; assert settings.embeddings_cache_max_entries == 100_000; assert settings.embeddings_cache_ttl_days == 180"`
  - Fact verified: `fact-config-retention`

### Step 2: Fix Config Binding & Add Vacuum to `EmbeddingCache` in `src/rubygemdb/services/embedding_cache.py`
- **Files**: `src/rubygemdb/services/embedding_cache.py`
- **Actions**:
  - In `EmbeddingCache.__init__`:
    - Change `self.db_path = Path(db_path or settings.cache_dir / "embeddings_cache.sqlite")` to:
      `self.db_path = Path(db_path or settings.embeddings_cache_file)`
  - In `EmbeddingCache.clear(self, model: Optional[str] = None, vacuum: bool = True) -> int`:
    - Delete target rows in transaction.
    - If `vacuum` is True, execute `VACUUM` on a separate non-transaction connection (`isolation_level = None`) so SQLite immediately shrinks the `.sqlite` file on disk and resets WAL/SHM allocations.
  - In `EmbeddingCache.stats()`:
    - Compute `db_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0`.
    - Check for WAL (`.sqlite-wal`) and SHM (`.sqlite-shm`) files and sum `total_disk_bytes`.
    - Include `db_file_bytes`, `wal_file_bytes`, and `total_disk_bytes` in returned stats dictionary.
- **Verification**:
  - Run `uv run python -c "from rubygemdb.services.embedding_cache import EmbeddingCache; from rubygemdb.core.config import settings; c = EmbeddingCache(); assert c.db_path == settings.embeddings_cache_file"`
  - Fact verified: `fact-config-binding`, `fact-vacuum-clear`

### Step 3: Implement Pruning & Opportunistic Eviction in `src/rubygemdb/services/embedding_cache.py`
- **Files**: `src/rubygemdb/services/embedding_cache.py`
- **Actions**:
  - Implement `prune(self, max_age_days: Optional[int] = None, max_entries: Optional[int] = None, vacuum: Optional[bool] = None) -> Dict[str, Any]`:
    - Purge entries where `created_at < (now - max_age_days * 86400)`.
    - If entry count > `max_entries`, delete `count - max_entries` oldest entries via `ORDER BY created_at ASC`.
    - If `vacuum is True` or (`vacuum is None` and deleted count > 10% of entries and deleted count > 0), run `VACUUM`.
    - Return summary dict: `{"deleted_expired": ..., "deleted_capacity": ..., "total_deleted": ..., "vacuumed": ...}`.
  - In `put_many`:
    - After inserting records, perform a lightweight opportunistic check against `settings.embeddings_cache_max_entries` (e.g., if batch size > 0 and random sample or periodically every N writes).
    - If current row count exceeds `settings.embeddings_cache_max_entries`, trigger `prune(max_age_days=settings.embeddings_cache_ttl_days, max_entries=settings.embeddings_cache_max_entries)`.
- **Verification**:
  - Unit test inserting vectors with synthetic timestamps and verifying TTL and capacity truncation.
  - Fact verified: `fact-prune-method`, `fact-inline-pruning`

### Step 4: Add CLI Cache Subcommands & Reclassify Integration in `src/rubygemdb/cli.py`
- **Files**: `src/rubygemdb/cli.py`
- **Actions**:
  - Create handler `run_cache(args)`:
    - `stats`: Print formatted Rich table showing Total Vectors, Approximate Vector Bytes, DB File Size, WAL File Size, Total Disk Footprint, and Model Breakdown.
    - `clear`: Call `EmbeddingCache.clear(model=args.model, vacuum=not args.no_vacuum)` and display success message with deleted count.
    - `prune`: Call `EmbeddingCache.prune(max_age_days=args.max_age_days, max_entries=args.max_entries, vacuum=not args.no_vacuum)` and display summary table.
  - Add `cache` subparser to `run_cli()` with `stats`, `clear`, and `prune` actions.
  - In `reclassify` parser:
    - Add `--clear-embeddings-cache` flag (action="store_true", help="Wipe and vacuum the universal vector embedding cache alongside classified data").
  - In `run_reclassify(args)`:
    - If `args.clear_embeddings_cache`: instantiate `EmbeddingCache()`, call `clear(vacuum=True)`, and print confirmation message.
- **Verification**:
  - Run `uv run rubygemdb cache stats`
  - Run `uv run rubygemdb cache --help`
  - Run `uv run rubygemdb reclassify --help`
  - Fact verified: `fact-cli-cache-subcommand`, `fact-cli-reclassify-flag`

### Step 5: Comprehensive Automated Testing in `tests/test_embedding_cache.py` & `tests/test_cli.py`
- **Files**: `tests/test_embedding_cache.py`, `tests/test_cli.py`
- **Actions**:
  - In `tests/test_embedding_cache.py`:
    - Add `test_embedding_cache_default_db_path_reads_settings`: test that `EmbeddingCache()` uses `settings.embeddings_cache_file`, and test that modifying `settings.embeddings_cache_file` updates the default path.
    - Add `test_embedding_cache_clear_with_vacuum`: test that clearing entries with `vacuum=True` executes without error and reclaims file space.
    - Add `test_embedding_cache_prune_by_ttl`: insert entries with backdated `created_at` and verify `prune(max_age_days=...)` removes only expired items.
    - Add `test_embedding_cache_prune_by_capacity`: insert N entries and verify `prune(max_entries=...)` keeps only the newest items.
    - Add `test_embedding_cache_opportunistic_inline_pruning`: configure low max entries, call `put_many`, and verify automatic trimming.
    - Add `test_embedding_cache_stats_file_sizes`: verify file size metrics are reported.
  - In `tests/test_cli.py` (or new test file):
    - Test CLI commands `cache stats`, `cache clear`, `cache prune`, and `reclassify --clear-embeddings-cache`.
- **Verification**:
  - Run `uv run pytest tests/test_embedding_cache.py tests/test_cli.py`
  - Fact verified: `fact-test-suite`

### Step 6: Update Documentation Across the Repository
- **Files**: `AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`
- **Actions**:
  - Remove the growth caveat ("No TTL or size cap — file grows until EmbeddingCache.clear() is called from Python").
  - Document `settings.embeddings_cache_max_entries` (100,000) and `settings.embeddings_cache_ttl_days` (180).
  - Document `rubygemdb cache {stats, clear, prune}` commands.
  - Document `rubygemdb reclassify --clear-embeddings-cache` flag.
- **Verification**:
  - Grep for "No TTL or size cap" across all markdown files to confirm 0 instances remain.
  - Fact verified: `fact-documentation-update`

---

## Risks & Mitigations
- **SQLite VACUUM Locking & Transaction Error**: `VACUUM` cannot run inside an open SQLite transaction. *Mitigation*: Ensure `clear()` and `prune()` execute `VACUUM` on an isolated connection with `isolation_level = None` outside of active transactions.
- **WAL File Retention**: WAL files (`.sqlite-wal`) may linger until checkpoints occur. *Mitigation*: Run `PRAGMA wal_checkpoint(TRUNCATE);` before `VACUUM` during clear/prune to ensure immediate cleanup.
- **Performance Overhead of Opportunistic Pruning**: Running `SELECT COUNT(*)` on every single `put` could slow down indexing. *Mitigation*: Only check row counts during `put_many` batch insertions, or sample periodically.
