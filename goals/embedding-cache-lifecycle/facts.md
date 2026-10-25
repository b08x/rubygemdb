# Facts

- EmbeddingCache default db_path reads settings.embeddings_cache_file directly from core/config.py, enabling environment and test configuration overrides.
- Settings in core/config.py defines embeddings_cache_max_entries (default: 100,000) and embeddings_cache_ttl_days (default: 180) with environment variable mapping.
- EmbeddingCache.clear() executes VACUUM by default when deleting records (with a vacuum=True parameter) to immediately reclaim disk space on the host filesystem.
- EmbeddingCache implements a prune(max_age_days=..., max_entries=..., vacuum=...) method that purges expired records and trims oldest entries by created_at, vacuuming when >10% of entries are pruned.
- EmbeddingCache opportunistically triggers retention pruning during put_many batch writes when table entry count exceeds settings.embeddings_cache_max_entries.
- The CLI provides a dedicated 'cache' command with 'stats', 'clear', and 'prune' subcommands displaying formatted Rich tables of vector counts, models, and disk sizes.
- The 'rubygemdb reclassify' CLI command supports a '--clear-embeddings-cache' flag that purges and vacuums the embedding cache alongside classified data.
- Project documentation (AGENTS.md, GEMINI.md, CLAUDE.md, and README.md) removes the unbounded growth caveat and details the retention limits, pruning behavior, and 'rubygemdb cache' CLI commands.
- Automated tests verify settings path override resolution, vacuum execution on clear, TTL and max-entry pruning logic, inline put_many pruning, and CLI cache subcommands.
