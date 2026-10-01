# Goal: Category Regrouping for RubyGemDB

## Articulated Goal

Replace RubyGemDB's 12 abstract architectural categories with 21 functional, human-obvious categories (document_parsing, cli_libraries, static_site_generation, ...) defined in a single taxonomy module, and rebuild classification so gems land where a person expects — combining keyword rules, RubyGems description-text scoring, and txtai embedding similarity, with the LLM fallback intact. The taxonomy is oriented around the project's primary purpose — assisting back-end tooling design — so every category carries a stack layer (substrate, plumbing, composition, quality) and the agent can scaffold a recommended gem stack (base gems like dotenv, drydock, pry, rubocop, journald-logger first) from a context query. A fresh-start reclassification wipes and rebuilds all classified data (SQLite, classified_gems.json, YAML outputs, txtai index) from the pruned inventory, following an approval-gated prune report for overlapping and stale gems.

## Shared Understanding

See [facts.md](facts.md) — 35 accepted facts covering the 21 categories with subcategory vocabularies, the classification pipeline, the fresh-start migration with prune report, the layer/stacking model, and the litmus gems (jekyll, nokogiri, thor, sidekiq, ruby_llm).

## Execution Plan

See [plan.md](plan.md) — 9 ordered steps: taxonomy module → classifier rebuild → embedding scorer → LLM prompt → TUI wiring → prune/reclassify command → layer-aware RAG and `stack` command → litmus test suite → documentation. Each step lists files touched and verification commands.

## Done Condition

All 35 facts hold on the real codebase: the litmus pytest suite passes (`uv run pytest tests/`), no old slug appears anywhere under `src/` or in any data store, the pruned-inventory reclassification produces agreeing SQLite/JSON/txtai/YAML outputs under the new taxonomy, `rubygemdb stack "<context>"` emits a valid layered manifest plus Gemfile snippet, and the agent instruction docs describe the new taxonomy.
