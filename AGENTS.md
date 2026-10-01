# AGENTS.md

Guide for AI agents working with the RubyGemDB codebase.

---

## Project Overview

RubyGemDB is a Ruby gem analysis and classification tool that categorizes gems into architectural patterns using heuristic analysis and LLM fallback for ambiguous cases.

### Functional Categories
21 functional categories, each tagged with a stack layer (`substrate`, `plumbing`, `composition`, `quality`), defined in a single module: `src/rubygemdb/models/categories.py`. The taxonomy is oriented around the project's primary purpose — assisting back-end tooling design — so search and retrieval can present gems as a layered stack.

**substrate** (runtime base everything stands on):
1. `core_extensions` — Core Extensions (activesupport, facets, bigdecimal, securerandom, dotenv)
2. `native_bindings` — Native Bindings (ffi, fiddle, rake-compiler, ruby-macho)
3. `servers_concurrency` — Servers & Concurrency (puma, falcon, async, concurrent-ruby, childprocess)

**plumbing** (data in/out and processing):
4. `http_networking` — HTTP & Networking (faraday, httparty, excon, aws-sdk-s3, google-apis-drive_v3)
5. `persistence` — Persistence (activerecord, sequel, pg, sqlite3, redis, shrine)
6. `background_jobs` — Background Jobs (sidekiq, solid_queue, resque, gush, async-job)
7. `document_parsing` — Document Parsing (nokogiri, rexml, csv, pdf-reader, commonmarker, yajl-ruby)
8. `document_generation` — Document Generation (prawn, pdfkit, wicked_pdf, asciidoctor-pdf, rouge)
9. `text_search` — Text & Search (pragmatic_segmenter, amatch, elasticsearch, searchkick, bm25f)
10. `media_processing` — Media Processing (ruby-vips, wavefile, taglib-ruby, ruby-sox, aubio)

**composition** (the shape of the application being built):
11. `web_frameworks` — Web Frameworks (rails, sinatra, roda, rack, haml, turbo-rails)
12. `cli_libraries` — CLI Libraries (thor, gli, drydock, highline; sub-categories: cli_frameworks, terminal_ui, terminal_styling, terminal_output)
13. `gui_desktop` — GUI & Desktop (glimmer-dsl-libui, tk, gosu)
14. `static_site_generation` — Static Site Generation (jekyll, cvless, hacked-jekyll, asciidoctor, beckett)
15. `ai_llm` — AI & LLM (ruby_llm, ruby-openai, rllama; sub-categories: llm_clients, agent_frameworks, embeddings_vector, prompt_tooling)
16. `mcp_tooling` — MCP Tooling (ruby-mcp-client, ruby_llm-mcp)
17. `runtime_validation` — Runtime Validation (dry-schema, dry-types, activemodel, schematist, hashie)
18. `security_auth` — Security & Auth (devise, pundit, bcrypt_pbkdf, ed25519, symmetric-encryption)

**quality** (correctness and maintainability of the stack):
19. `testing_qa` — Testing & QA (rspec, capybara, factory_bot, webmock, selenium-webdriver)
20. `code_quality_typing` — Code Quality & Typing (rubocop, brakeman, sorbet, rbs, ruby-lsp)
21. `developer_tools` — Developer Tools (pry, debug, stackprof, vernier, bundler-audit)

Each category also carries curated `base_gems` stack seeds (e.g. dotenv, drydock, pry, rubocop, journald-logger) used by the `stack` command and the RAG agent to scaffold recommended gem stacks for back-end tooling.

---

## Architecture

### Data Flow
1. Read gem inventory from CSV → Fetch RubyGems API data → Heuristic classification → LLM enhancement (optional) → Save results to YAML/SQLite
2. Caching layers to minimize redundant API/LLM calls
3. Persistent storage for classified gem results

### Core Component Directory Structure
```
src/rubygemdb/
├── agent.py            # Layer-aware txtai RAG agent (smolagents)
├── cli.py              # Command-line interface (process / reclassify / stack)
├── ui/
│   └── tui.py          # Interactive Textual TUI explorer
├── core/
│   └── config.py       # Settings and environment variable handling
├── models/
│   ├── gem.py          # Pydantic data models for gem data
│   └── categories.py   # Single source of truth: 21 categories + layers
├── services/
│   ├── classifier.py   # Keyword/description/embedding classification logic
│   ├── embedding_scorer.py # txtai embedding similarity vs category descriptions
│   ├── prune.py        # Overlap/stale gem prune reports
│   ├── stack.py        # Layered gem-stack composition
│   ├── rubygems.py     # RubyGems API client with caching
│   ├── llm.py          # Mistral LLM client for fallback classification
│   └── context7.py     # Context7 API client for cheatsheet generation
└── storage/
    ├── base.py         # Storage interface
    ├── json_storage.py # JSON file storage implementation
    └── sqlite_storage.py # SQLite storage implementation
```

---

## Key Commands

### Install Dependencies
```bash
# Using uv (recommended) - CPU default
uv sync --extra cpu

# With optional CUDA support (NVIDIA GPU acceleration)
uv sync --extra cuda

# Or pip
pip install -e .
pip install -r dev-requirements.txt
```

### Batch Classification (CLI)
Process a CSV of gem names and generate YAML output reports:
```bash
# Direct execution
python -m rubygemdb gems-inventory.csv --out output/

# As installed package
rubygemdb gems-inventory.csv --out output/

# With uv
uv run rubygemdb gems-inventory.csv --out output/
```

### Interactive TUI Explorer
Launch terminal UI for browsing and managing classified gems:
```bash
# Direct execution
python -m rubygemdb.ui.tui [optional-gems-inventory.csv]

# As installed package
rubygemdb-tui [optional-gems-inventory.csv]

# With uv
uv run rubygemdb-tui [optional-gems-inventory.csv]
```

### Reclassification (fresh start under the current taxonomy)
Produces an approval-gated prune report (`data/prune-report.md` listing overlapping and stale gems — no release in 3+ years), then wipes and rebuilds all classified data (SQLite `classified_gems` table, `classified_gems.json`, txtai index, per-category YAMLs) from the pruned inventory:
```bash
# Review the prune report first, then apply it
uv run rubygemdb reclassify --out output/          # interactive prune confirmation
uv run rubygemdb reclassify --apply-prune --embed  # non-interactive: full report + rebuild txtai index
uv run rubygemdb reclassify --prune-list prune.txt --embed  # prune only the gems named in the file
```
The prune list file is plain text: one gem name per line, `#` comments allowed — use it to approve a subset instead of the whole report.
A dated SQLite backup (`data/rubygemdb.sqlite.bak-<date>`) is written before anything is wiped. Run with ollama up for best embedding-assisted classification.

### Gem-Stack Composition
Turn a context query into a layered stack manifest (layer, category, gem, role) plus a Gemfile snippet, starting from the curated base picks (dotenv, drydock, pry, rubocop, journald-logger):
```bash
uv run rubygemdb stack "CLI data pipeline tool"           # print to stdout
uv run rubygemdb stack "CLI data pipeline tool" --out stacks/  # also write files
```
Output is advisory, not a dependency resolver. Deeper Rubysmith project generation from a stack manifest is a follow-up goal.

### Linting & Type Checking
```bash
# Ruff for linting
uv run ruff check src/rubygemdb/

# Mypy for type checking
uv run mypy src/rubygemdb/
```

---

## Environment Variables

Required for full functionality:
```bash
# Devstral API key for LLM fallback classification
export DEVSTRAL_API_KEY="your_devstral_key"

# Context7 API key for generating gem usage cheatsheets
export CONTEXT7_API_KEY="your_context7_key"
```

---

## Classification Logic


### Dynamic RAG Workflow
1. User enters a query (optionally selecting a target codebase project from the TUI dropdown).
2. The query is processed by the "Other Steve" Prompt Architect to generate strict, SFL-compliant semantic variations.
3. The variations are searched against the `txtai` database (documents carry `category` and `layer` metadata), applying a 30% recency boost for updated gems.
4. For back-end tooling queries, results are presented grouped by layer (substrate first, quality last), and recommendations start from the category `base_gems` seeds before context-specific gems.
5. If a target project is selected, the agent calls `get_architecture` and `search_graph` via Codebase Memory MCP to inspect the target structure.
6. The agent synthesizes the Context7 cheatsheets, semantic gem matches, and actual codebase structure to output a strict Pragmatic Implementation Backlog.

### Classification Pipeline
Classification combines three scoring signals; the best-scoring category wins:
- Keyword rules: gem name (token match) and dependency substrings, re-keyed to the new slugs via `categories.py`
- Description-text scoring: RubyGems `info` term hits per category keywords/description
- txtai embedding similarity (`EmbeddingScorer`, model `ollama/embeddinggemma`) between `"{name}: {description}"` and category description vectors; degrades gracefully to keyword + description scoring when ollama is down

Name-keyword matches score 0.8 confidence; dependency-only 0.65; description/embedding-only ≤ 0.6; unknown gems default to `core_extensions` at 0.4.

### LLM Fallback
Triggers when heuristic confidence < 0.7: Uses Devstral API to refine classification with 0.6 minimum confidence threshold to override heuristics.

---

## Caching Strategy

Local cache files:
1. `gem_cache.json`: Raw RubyGems API responses
2. `llm_cache.json`: SHA256-hashed LLM prompt/response pairs
3. `classified_gems.json`: Persisted TUI classification results
4. `data/rubygemdb.sqlite`: Central SQLite database for inventory and classified gems
5. `data/txtai/`: Layer-aware txtai embedding index
6. `data/cache/category_vectors/`: Cached category-description vectors for embedding scoring
7. `data/prune-report.md`: Approval-gated overlap/stale gem report

---

## Development Workflow

1. Install dependencies with `uv sync --dev`
2. Make changes to code in `src/rubygemdb/`
3. Test with local execution: `uv run rubygemdb data/gems-inventory.csv --out test-output/`
4. Lint and type check: `uv run ruff check && uv run mypy`

---

## Data Formats

### Input CSV
Requires either `name` or `gem` column for gem names. Optional columns:
- `group`: Development/test context
- `category`: Initial category hint
- `description`: Gem description
- `homepage`: Gem homepage URL
- `context7_id`: Context7 library ID for cheatsheet generation

### Output YAML
Per-category YAML files contain structured gem classification data:
```yaml
- name: gem_name
  classification:
    primary: category_name  # one of the 21 slugs
    confidence: 0.85
  role:
    description: "Gem metadata description"
    attaches_to: "category_prefix"
  risks:
    invasiveness: 3
    coupling: 2
    abstraction_leak: "low"
  signals:
    rails: true
    external_io: false
    native_ext: false
  dependencies: ["activesupport"]
```

---

## Gotchas & Non-Obvious Patterns

1. **Confidence Thresholds**: Heuristic results <0.7 trigger LLM processing; LLM results must have >0.6 confidence to override heuristics
2. **Cache Persistence**: Cache files are automatically saved after successful API/LLM calls
3. **Rate Limiting**: Built-in delays between API requests: 0.1s between RubyGems calls, 0.2s between LLM calls
4. **Batch Size**: Default LLM batch size is 10 for efficient bulk processing
5. **TUI Requirements**: CSV input only required for first run to populate SQLite database

---

## Testing

The litmus pytest suite verifies taxonomy integrity, classification expectations (jekyll→static_site_generation, nokogiri→document_parsing, thor→cli_libraries, sidekiq→background_jobs, ruby_llm→ai_llm), migration completeness (no old slugs under `src/`), and stack composition:
1. Run the suite: `uv run pytest tests/`
2. Validate classification logic with sample gems: `uv run python -m rubygemdb data/sample-gems.csv --out test-output/`

## Existing Documentation

Additional context available in:
- `CLAUDE.md`: Legacy Claude-specific guidance
- `GEMINI.md`: Original project documentation