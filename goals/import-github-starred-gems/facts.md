# Facts

- A GitHub starred repo service fetches paginated starred repositories for a user (defaulting to b08x) filtered by language (defaulting to Ruby).
- Starred repositories are resolved to gem names via root *.gemspec inspection or repo name, verified against rubygems.org via RubyGemsService, and non-gem repositories are skipped.
- Existing gems in the SQLite inventory table are not duplicated; any missing metadata fields (source_code_uri, homepage, context7_id) are backfilled and enriched from GitHub and RubyGems.
- The SQLite schema (inventory and classified_gems tables) and Pydantic models (GemEntry and GemInventoryItem) include a dedicated genai_uses column for GenAI backend tooling use cases.
- Inferred GenAI backend tooling uses (such as agent memory, prompt tooling, tool harness, ETL/preprocessing, and eval) are generated via heuristic rules and LLM fallback, then persisted to genai_uses.
- An audit pass evaluates the merged inventory with PruneService, detecting stale gems (3+ years without release), name-normalization overlaps, and shared monorepo URIs, writing data/prune-report.md.
- No candidate gems from the prune report are removed unless the user explicitly passes --apply-prune or specifies an approved prune list.
- A new CLI command `rubygemdb import-stars` is available with options --user, --language, --dry-run, --classify, --embed, and --apply-prune.
- Running `rubygemdb import-stars --dry-run` reports discovered gems, matched gems, enriched records, and prune candidates without modifying the SQLite database or disk files.
- When --classify and --embed are passed, imported gems are categorized into the 21 architectural categories with sub-category facets and indexed into the txtai vector database.
- Automated pytest tests cover GitHub repository fetching, gem name resolution, deduplication/enrichment logic, genai_uses schema migration and inference, and import-stars CLI flags.
