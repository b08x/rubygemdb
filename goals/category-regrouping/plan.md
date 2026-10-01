# Plan: Category Regrouping for RubyGemDB (rev 2)

## Strategic Framing

RubyGemDB's primary purpose is to assist in the design of back-end tooling. The category system therefore exists to do more than sort gems: search, retrieval, and groupings must reflect a **stacking/layering** model. Base gems (dotenv, drydock, pry, rubocop, journald-logger — or the stdlib logger on non-systemd hosts) sit at the bottom of any back-end tool scaffold; given a context, the agent intelligently stacks the appropriate gems on top, layer by layer. This plan delivers the taxonomy regrouping and makes the layering explicit in the data model, the RAG index, and a new `stack` command that emits scaffolding-ready output. Deep Rubysmith integration (generating an actual project skeleton from a recommended stack) is explicitly deferred to a follow-up goal.

## Solution Approach

Replace the 12 abstract architectural categories with 21 functional categories, each tagged with a stack layer, defined in a single taxonomy module. Rebuild classification around three scoring signals — name/dependency keyword rules, RubyGems description-text scoring, and txtai embedding similarity against category descriptions — with the existing LLM fallback intact. Add a `reclassify` CLI command that produces an approval-gated prune report, wipes all classified data (fresh start), and rebuilds SQLite, `classified_gems.json`, YAML outputs, and the layer-aware txtai index from the pruned inventory. Add a `stack` command that turns a context query into a layered gem-stack manifest plus a Gemfile snippet. Verify with a litmus pytest suite and grep-based checks.

## Layer Model

Four layers, assigned to every category:

| Layer | Meaning | Categories |
|---|---|---|
| `substrate` | runtime base everything stands on | core_extensions, native_bindings, servers_concurrency |
| `plumbing` | data in/out and processing | http_networking, persistence, background_jobs, document_parsing, document_generation, text_search, media_processing |
| `composition` | the shape of the application being built | web_frameworks, cli_libraries, gui_desktop, static_site_generation, ai_llm, mcp_tooling, runtime_validation, security_auth |
| `quality` | correctness and maintainability of the stack | testing_qa, code_quality_typing, developer_tools |

Common base picks (stack seeds) curated per layer — e.g. dotenv (substrate), pry + rubocop (quality), drydock (composition for CLI contexts), and journald-logger for structured logging on systemd hosts, falling back to the stdlib `logger` gem on non-systemd hosts. The agent uses these as scaffolding defaults and adds context-specific gems above them.

## Ordered Steps

### Step 1 — Create the taxonomy module
**Files:** new `src/rubygemdb/models/categories.py`

- `Category` pydantic model: `slug`, `label` (readable), `description` (also serves as the embedding anchor text), `layer` (substrate | plumbing | composition | quality), `keywords` (name + dependency match terms), `sub_categories` (slug → example gems), `base_gems` (curated stack seeds for back-end tooling, e.g. dotenv, drydock, pry, rubocop, journald-logger), `default_invasiveness`, `default_abstraction_leak`.
- `CATEGORIES`: the 21 accepted slugs — web_frameworks, http_networking, servers_concurrency, background_jobs, persistence, document_parsing, document_generation, static_site_generation, cli_libraries, testing_qa, code_quality_typing, runtime_validation, developer_tools, ai_llm, mcp_tooling, text_search, security_auth, media_processing, gui_desktop, native_bindings, core_extensions — each with its layer per the table above.
- Sub-category vocabularies for `cli_libraries` (cli_frameworks, terminal_ui, terminal_styling, terminal_output) and `ai_llm` (llm_clients, agent_frameworks, embeddings_vector, prompt_tooling).
- `VALID_CATEGORIES`, `CATEGORY_BY_SLUG`, `LAYERS` exported from here.

**Verification:** `uv run python -c "from rubygemdb.models.categories import CATEGORIES; assert len(CATEGORIES) == 21; assert all(c.layer in {'substrate','plumbing','composition','quality'} for c in CATEGORIES)"`.

### Step 2 — Rebuild the classifier
**Files:** `src/rubygemdb/services/classifier.py`

- Delete the local `VALID_CATEGORIES`; import from the taxonomy module.
- Rewrite `heuristic_classify`: per-category keyword rules (name + dependency substrings re-keyed to new slugs) plus description-text scoring over `info["info"]` (term hits per category keywords/description terms, so gems like jekyll land correctly despite quiet names).
- Re-key `score_gem` risk maps to new slugs; unknown slug falls back to defaults (invasiveness 3, leak "low") without raising.
- Replace `attaches_to` derivation (`primary.split("_")[0]`) with the category label or slug prefix from the module.
- Keep the confidence < 0.7 LLM-override contract unchanged.

**Verification:** `uv run ruff check src/rubygemdb/ && uv run mypy src/rubygemdb/`; litmus tests (Step 8).

### Step 3 — Embedding-assisted scoring
**Files:** new `src/rubygemdb/services/embedding_scorer.py`, touched `src/rubygemdb/services/classifier.py`

- `EmbeddingScorer`: txtai `Embeddings` in similarity mode using the same model as `agent.py` (`ollama/embeddinggemma`). Embeds `"{name}: {description}"` per gem, compares against precomputed category-description vectors (computed once, cached to `data/cache/category_vectors`).
- Classifier calls it after keyword/description scoring; final score = normalized blend, best category wins.
- Graceful degradation: if ollama/txtai is unavailable, log and fall back to keyword + description scoring only (facts stay satisfiable without a running model).

**Verification:** unit test with a mocked scorer; manual run of `reclassify` with ollama up.

### Step 4 — LLM prompt uses the module
**Files:** `src/rubygemdb/services/llm.py`

- Replace the hardcoded 12-item list in `build_prompt` with an import from the taxonomy module; render each category as `- slug (layer): description` so the LLM sees the boundaries and the stacking context.

**Verification:** unit test asserting every new slug appears in `build_prompt` output and no old slug does.

### Step 5 — TUI wiring
**Files:** `src/rubygemdb/ui/tui.py`

- Import categories from the module (drop the classifier import of the old list).
- Class-filter RadioSet and the Edit Category radio set render `label` (readable) with `cls_{slug}` ids.
- Ensure manual edits (confidence 1.0) keep working through `update_gem_classification`.

**Verification:** manual `uv run rubygemdb-tui` pass; grep test (Step 8) covers slug removal.

### Step 6 — Prune report + reclassify command
**Files:** `src/rubygemdb/cli.py`, new `src/rubygemdb/services/prune.py`, touched `src/rubygemdb/storage/sqlite_storage.py`, `src/rubygemdb/storage/json_storage.py`

- `prune.py`: builds the prune report — (a) overlap candidates: name-normalization duplicates (e.g. `standardrb` vs `standard`) and gems sharing a normalized `source_code_uri`; (b) stale gems: `version_created_at` older than 3 years, sourced from the gem cache (`data/cache/gem_cache.json`, 535 entries) with fetch fallback for missing gems. Writes `data/prune-report.md` for review.
- `rubygemdb reclassify [--apply-prune]`:
  1. Back up `data/rubygemdb.sqlite` → `data/rubygemdb.sqlite.bak-<date>` (fresh start is destructive).
  2. Show prune report; with `--apply-prune` (or interactive confirm) delete pruned gems via `delete_gem`.
  3. Wipe `classified_gems` table, `data/classified_gems.json`, and the txtai index dir (`data/txtai`).
  4. Re-classify every remaining inventory gem via the classifier (LLM runs per existing behavior; llm_cache absorbs repeats).
  5. Save to SQLite, regenerate `classified_gems.json` from SQLite, write per-category YAMLs to `--out`, and rebuild the txtai index via `TxtaiAgent.build_index()` (skipped with a warning if ollama is down).

**Verification:** run on a copy of the real DB: counts of inventory == classified == json == txtai source rows; no old slugs in any store; YAML files named by new slugs.

### Step 7 — Layer-aware RAG and stacking
**Files:** `src/rubygemdb/agent.py`, new `src/rubygemdb/services/stack.py`

- `agent.py` `_initialize_index`: include `layer` and `category` in each indexed document's metadata (already indexes name/description; add the two fields from the taxonomy module) so search results carry stacking context.
- Agent prompt (`agent()` tool descriptions / system framing): for back-end tooling queries, present results grouped by layer, substrate first, and start recommendations from `base_gems` seeds before context-specific additions.
- `stack.py` + `rubygemdb stack "<context query>"`:
  1. Embed the query, retrieve candidate gems via the txtai index.
  2. Group candidates by layer (substrate → quality), inject the curated `base_gems` seeds for each relevant layer.
  3. Emit a stack manifest (YAML: layer, category, gem, role/why) and a Gemfile snippet to stdout or `--out`.
  4. Any seed gem missing from the inventory (e.g. journald-logger) is added via `add_gem_to_inventory` so seeds are always retrievable.
- Rubysmith: the manifest is structured so a follow-up goal can feed Rubysmith's template system to generate the actual project skeleton; no Rubysmith integration in this goal.

**Verification:** `uv run rubygemdb stack "CLI data pipeline tool"` produces a manifest with all four layers present and a valid Gemfile snippet; unit test with a mocked index.

### Step 8 — Litmus test suite
**Files:** new `tests/test_categories.py`, `tests/test_classifier_litmus.py`, `tests/test_taxonomy_migration.py`, `tests/test_stack.py`

- Mock RubyGems/LLM/scorer services; assert: jekyll→static_site_generation, nokogiri→document_parsing, thor→cli_libraries, sidekiq→background_jobs, ruby_llm→ai_llm.
- Taxonomy integrity: 21 categories, unique slugs, labels present, every category has a valid layer, subcategory vocab for cli_libraries/ai_llm, base_gems non-empty for substrate/quality.
- Migration: prompt contains all new slugs; `grep -r` over `src/` for the 12 old slugs returns nothing; risk fallback returns defaults for an unknown slug.
- Stack: manifest layers ordered substrate→quality; base seeds appear.

**Verification:** `uv run pytest tests/`.

### Step 9 — Documentation
**Files:** `AGENTS.md`, plus `CLAUDE.md`/`GEMINI.md` where they list categories

- Replace the 12-category section with the 21 new slugs, labels, layers, descriptions, subcategory vocabularies, and the stacking workflow (search → layer grouping → `stack` command → Rubysmith follow-up).

**Verification:** manual diff review; slug list matches `categories.py` exactly.

## Risks and Open Questions

- **Fresh start is irreversible.** 492 classified rows (including manual confidence-1.0 overrides) are deleted. The plan adds a dated SQLite backup before wiping; the txtai index dir wipe is total.
- **Embedding scorer depends on a running ollama instance.** Without it, classification falls back to keyword + description scoring — lower precision for quiet-named gems, but functional. The `reclassify` run should be executed with ollama up for best results.
- **LLM cost/time on reclassify.** `classify()` calls the LLM unconditionally for agent descriptions (~490 gems; cached responses soften repeats). If this run is too expensive, a follow-up could gate the agent-description call — out of scope here.
- **Prune overlap detection is heuristic** (name/URI normalization). It's approval-gated, so false positives are visible and harmless; false negatives just mean slightly more gems retained.
- **`version_created_at` availability.** The gem cache covers all 535 known gems today; uncached gems trigger a RubyGems fetch inside the prune step.
- **Layer assignments are a first draft** (the table above). They are data in `categories.py`, not code, so re-tiering a category later is a one-line change.
- **`stack` output is advisory, not a dependency resolver.** It recommends and scaffolds; it does not check version compatibility. Rubysmith generation would add that, hence the follow-up.
- **21 categories exceeds the original 10-16 estimate**, driven by requested splits (gui_desktop, subcategory vocabularies). Low-cost cuts if needed: merge `runtime_validation` into `code_quality_typing`, `document_generation` into `document_parsing`.
