# Goal: Universal Embedding Cache Lifecycle, Retention, and Configuration

## Articulated Goal
Resolve dead configuration binding and unbounded storage growth in RubyGemDB's universal vector embedding cache. Wire `EmbeddingCache` directly to `settings.embeddings_cache_file`, establish configurable capacity and TTL retention policies (`embeddings_cache_max_entries`, `embeddings_cache_ttl_days`), implement physical disk reclamation via `VACUUM`, and provide dedicated operator CLI management commands (`rubygemdb cache {stats, clear, prune}`) and reclassification integration (`--clear-embeddings-cache`).

## Shared Understanding
- [facts.md](facts.md): The accepted, testable facts defining configuration binding, retention limits, VACUUM execution, pruning routines, CLI commands, documentation updates, and test verification requirements.

## Execution Plan
- [plan.md](plan.md): The approved 6-step implementation plan covering settings configuration, `EmbeddingCache` constructor and vacuum enhancements, TTL and capacity pruning, CLI command group integration, comprehensive unit testing, and documentation alignment.

## Done Condition
1. `EmbeddingCache.__init__` reads `settings.embeddings_cache_file` directly by default and respects configuration overrides.
2. `Settings` in `src/rubygemdb/core/config.py` defines `embeddings_cache_max_entries` (100,000) and `embeddings_cache_ttl_days` (180) with environment variable mappings.
3. `EmbeddingCache.clear()` and `EmbeddingCache.prune()` support SQLite `VACUUM` and checkpointing to physically shrink `.sqlite` and WAL file footprints on disk.
4. `EmbeddingCache.prune()` purges expired vectors by TTL and trims oldest records when exceeding max entry capacity, with opportunistic execution during `put_many`.
5. CLI subcommands `rubygemdb cache {stats, clear, prune}` and flag `rubygemdb reclassify --clear-embeddings-cache` are implemented and operational with Rich formatted outputs.
6. Documentation files (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`) are updated to remove the unbounded growth caveat and document new cache controls.
7. All tests in `tests/test_embedding_cache.py` and `tests/test_cli.py` pass cleanly (`uv run pytest tests/`).
