# Goal: Import GitHub Starred RubyGems into RubyGemDB

Import Ruby repositories starred by `@b08x` on GitHub into RubyGemDB's SQLite inventory database and classification catalog. The import verifies whether starred repositories are published gems, enriches existing and new records with source repository URLs, homepages, and inferred use cases for GenAI backend tooling, and runs an approval-gated deduplication and staleness prune audit.

## Shared Understanding

The shared understanding and testable outcomes are specified in [`facts.md`](facts.md) and [`facts.meta.json`](facts.meta.json).

Key capabilities established:
- Paginated retrieval of starred GitHub repositories filtered by language (Ruby) using authenticated tokens with `gh` CLI fallback.
- Gem resolution matching repository names or root `*.gemspec` files against rubygems.org via `RubyGemsService`, skipping non-gem repositories.
- Non-duplicating inventory merge that backfills and enriches existing gems.
- Schema migration and Pydantic models with a dedicated `genai_uses` column.
- Heuristic and LLM inference for GenAI backend tooling roles.
- Prune audit using `PruneService` generating `data/prune-report.md`, safe by default and requiring explicit `--apply-prune` or approval list to remove gems.
- New CLI subcommand `rubygemdb import-stars` supporting `--user`, `--language`, `--dry-run`, `--classify`, `--embed`, and `--apply-prune`.

## Execution Plan

The step-by-step implementation strategy and verification gates are detailed in [`plan.md`](plan.md).

## Done Condition

1. `rubygemdb import-stars` CLI command is fully functional with `--user`, `--language`, `--dry-run`, `--classify`, `--embed`, and `--apply-prune` options.
2. The SQLite schema (`inventory` and `classified_gems`) and Pydantic models contain `genai_uses`, populated with inferred GenAI backend tooling uses.
3. Existing gems in `data/rubygemdb.sqlite` are safely deduplicated and enriched without duplication.
4. `PruneService` executes during import and generates `data/prune-report.md`, with deletions occurring only when explicitly requested.
5. All automated unit and integration tests in `tests/test_import_stars.py` pass, and existing suite passes 100% (`uv run pytest tests/`).
