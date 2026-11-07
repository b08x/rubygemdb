# Plan: Refactor `rubygemdb` Skill, Move to `.skills/rubygemdb`, & Add Installer

## Solution Approach

Migrate the `rubygemdb` skill from `~/.syncopated/skills/rubygemdb` into the repository at `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/` (`SKILL.md`, `scripts/gem.py`, and `install.py`), and provide an installer (`uv run rubygemdb install-skill` / `python .skills/rubygemdb/install.py`) that deploys the skill to `~/.agents/skills/rubygemdb` (supporting both copy and `--link` symlink modes, plus `--target` override). Upgrade `.skills/rubygemdb/scripts/gem.py` so its subcommands (`search`, `info`, `stack`, `categories`, plus new `graphrag` and `sessions` subcommands) expose the latest service parameters, schema columns (`sub_categories`, `genai_uses`), and GraphRAG/session capabilities. Rewrite `.skills/rubygemdb/SKILL.md` around a 3-tier routing model (`rubygemdb-mcp` first, `scripts/gem.py` for CLI fallback and single-gem catalog mutations, and `uv run rubygemdb` for bulk operator workflows), documenting the `[C1]` grounded citation contract and current environment variables. Finally, add `tests/test_skill_gem_cli.py` testing the repo-local `.skills/rubygemdb` files and the installer directly without external host path dependencies.

---

## Ordered Steps

### 1. Migrate Skill to `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb` & Create Installer
**Files touched:**
- `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/SKILL.md` (moved from `~/.syncopated/skills/rubygemdb/SKILL.md`)
- `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py` (moved from `~/.syncopated/skills/rubygemdb/scripts/gem.py`)
- `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/install.py` (new standalone + importable installer)
- `/home/b08x/WorkspaceV3/rubygemdb/src/rubygemdb/cli.py` (wire `rubygemdb install-skill` subcommand)
- `/home/b08x/.gitignore` inside `/home/b08x/WorkspaceV3/rubygemdb/.gitignore` (verify `.skills/` is tracked and not ignored)

**Changes:**
- Move `SKILL.md` and `scripts/gem.py` from `~/.syncopated/skills/rubygemdb/` into `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/` and remove the old `~/.syncopated/skills/rubygemdb` directory once migrated.
- Create `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/install.py` with `install_skill(target_dir: Path = Path.home() / ".agents" / "skills", link: bool = False) -> Path`:
  - Copies `SKILL.md` and `scripts/gem.py` (preserving executable permissions on `scripts/gem.py`) into `<target_dir>/rubygemdb`, or creates/updates a symlink when `--link` is passed.
  - Exposes a CLI entrypoint (`python .skills/rubygemdb/install.py [--target ~/.agents/skills] [--link]`).
- Wire `uv run rubygemdb install-skill [--target DIR] [--link]` in `src/rubygemdb/cli.py` so operators can also deploy the skill directly from the main CLI.

**Verification:**
```bash
cd /home/b08x/WorkspaceV3/rubygemdb && uv run rubygemdb install-skill
test -f ~/.agents/skills/rubygemdb/SKILL.md
test -f ~/.agents/skills/rubygemdb/scripts/gem.py
```

---

### 2. Upgrade `.skills/rubygemdb/scripts/gem.py` Catalog, Stack, and Taxonomy Subcommands
**Files touched:**
- `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py`

**Changes:**
- **`search`** (Fact 5):
  - Add `--category <slug>` (passed through to `hybrid_search(..., category=args.category)`).
  - Add optional `--expand` and `--rerank` flags (and honor `settings.query_expansion_enabled` / `settings.reranker_enabled`) by lazily instantiating `QueryExpander(enabled=True)` / `CrossEncoderReranker(enabled=True)` when requested and passing `query_expander` and `reranker` into `hybrid_search()`.
- **`stack`** (Fact 6):
  - Add `--max-gems N` (default `10`) and pass `max_gems=args.max_gems` to `StackService.build_stack(args.query, max_gems=args.max_gems)`.
- **`categories` & `info`** (Fact 7):
  - In `_ensure_preferred_column()` (updated to `_ensure_schema_columns()`), ensure `preferred` and `genai_uses` on `inventory` and `sub_categories` / `genai_uses` on `classified_gems` are migrated safely when opening older DBs.
  - In `cmd_info`: read `sub_categories` from `classification.get("sub_categories")` with fallback to the `classified_gems.sub_categories` JSON column, and surface `genai_uses` from `classified_gems` or `inventory` in both `--json` and text output.
  - In `cmd_categories`: include `sub_categories` (list of facet slugs in summary mode; full `sub_categories` mapping `{facet_slug: [example_gems]}` when a category `<slug>` is inspected) in both text and `--json` output.

**Verification:**
```bash
uv run --project /home/b08x/WorkspaceV3/rubygemdb \
  python /home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py search "html parser" --category document_parsing --json
uv run --project /home/b08x/WorkspaceV3/rubygemdb \
  python /home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py stack "CLI data pipeline tool" --max-gems 6
uv run --project /home/b08x/WorkspaceV3/rubygemdb \
  python /home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py categories ai_llm --json
uv run --project /home/b08x/WorkspaceV3/rubygemdb \
  python /home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py info nokogiri --json
```

---

### 3. Add `graphrag` and `sessions` Subcommands to `.skills/rubygemdb/scripts/gem.py`
**Files touched:**
- `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py`

**Changes:**
- **`gem.py graphrag`** (Fact 8):
  - `gem.py graphrag build <gem> [<gem> ...] [--intention TEXT] [--session-id UUID] [--force] [--json]`:
    - Creates or updates a session in `SessionStorage`, runs `GraphRAGService().build_or_load_index(gems, force_rebuild=args.force)`, and outputs `session_id`, `gems`, `hash`, `nodes_count`, `edges_count`, and `status`.
  - `gem.py graphrag query <query> --gems <gem> [<gem> ...] [--intention TEXT] [--session-id UUID] [--top-k 5] [--json]`:
    - Builds/loads the gem index via `GraphRAGService`, calls `retrieve(query, top_k=args.top_k)` and `format_grounded_context(retrieval, intention=args.intention)`, appends the user/agent turn (including `retrieved_nodes` and `citations`) to `SessionStorage`, and prints either the grounded markdown context + `[C1]` citation summary + `session_id` (text mode) or the structured payload with `session_id`, `citations`, `grounded_context`, `primary_nodes`, `expanded_nodes`, and `edges` (`--json` mode).
- **`gem.py sessions`** (Fact 9):
  - `gem.py sessions list [--limit 20] [--json]`: lists tracked sessions from `SessionStorage` (`data/sessions.sqlite`).
  - `gem.py sessions show <session_id> [--json]`: displays session metadata, messages (with citation counts/tags), and traces.
  - `gem.py sessions export <session_id> [--format markdown|json] [--out FILE]`: exports transcript via `SessionStorage.export_transcript`.
  - `gem.py sessions delete <session_id> [--json]`: deletes session and reports status.

**Verification:**
```bash
uv run --project /home/b08x/WorkspaceV3/rubygemdb \
  python /home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py sessions list --json
uv run --project /home/b08x/WorkspaceV3/rubygemdb \
  python /home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py graphrag --help
```

---

### 4. Refactor `.skills/rubygemdb/SKILL.md` & Deploy to `~/.agents/skills/rubygemdb`
**Files touched:**
- `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/SKILL.md`
- `/home/b08x/.agents/skills/rubygemdb/` (via installer deployment)

**Changes:**
- **3-Tier Routing Model** (Fact 1):
  1. **Tier 1 — Native MCP Server (`rubygemdb-mcp`)**: Preferred when mounted in the host agent for read-only catalog queries, stack composition, containerized GraphRAG, and session persistence.
  2. **Tier 2 — Skill Script (`~/.agents/skills/rubygemdb/scripts/gem.py`)**: Fast CLI fallback when MCP is unavailable, and the designated tool for single-gem catalog mutations (`add`, `remove`, `prefer`, `unprefer`) since MCP catalog tools enforce `mode=ro`.
  3. **Tier 3 — Repository Operator CLI (`uv run rubygemdb ...`)**: Bulk maintenance, GitHub star imports, interactive/automated pruning, full reclassification, embedding cache lifecycle management, and `install-skill`.
- **Native MCP Server Reference** (Fact 2):
  - Document all catalog tools (`search_gems`, `get_gem`, `compose_stack`, `list_categories`, `build_gem_graphrag`, `query_gem_graphrag`), session tools (`list_rag_sessions`, `get_rag_session`, `resume_rag_session`, `export_rag_session_transcript`, `delete_rag_session`), resources (`rubygemdb://taxonomy`, `rubygemdb://stack-seeds`), prompts (`design-ruby-stack`, `gem-architect-rag`), and SSE daemon commands (`uv run rubygemdb-mcp --daemon start|status|stop --port 8011`).
- **Containerized GraphRAG & `[C1]` Citation Contract** (Fact 3):
  - Document how `rubygemdb-indexer:latest` extracts AST symbols (`rubydex`) and structured docs (`decode`) from shallow-cloned repos (or unpacked `.gem` archives), and state the grounded citation contract: inline `[C1]` tags with source files, closing `### Citations` block, strict adherence to retrieved APIs, and explicit distinction for custom application Ruby.
- **Updated `gem.py` Subcommands & Operator CLI Workflows** (Facts 4, 5, 6, 7, 8, 9):
  - Document updated `gem.py` flags (`search --category --expand --rerank`, `stack --max-gems`, `categories [slug]`, `info`, `graphrag build|query`, `sessions list|show|export|delete`).
  - Document bulk operator CLI commands: `import-stars`, `prune` (`gum`/`textual`), `reclassify` (including `--clear-embeddings-cache`), `cache` (`stats`/`clear`/`prune`), and `install-skill`.
- **Environment & Retrieval Knobs** (Fact 3):
  - Replace stale `DEVSTRAL_API_KEY` with `OPENROUTER_API_KEY` (and `MISTRAL_API_KEY` when `RUBYGEMDB_PROVIDER=mistral`), `CONTEXT7_API_KEY`, `GITHUB_TOKEN`, `QUERY_EXPANSION_ENABLED`, `RERANKER_ENABLED`, `EMBEDDINGS_CACHE_MAX_ENTRIES`, `EMBEDDINGS_CACHE_TTL_DAYS`, and `AGENT_MAX_TOKENS`.
- Re-run `uv run rubygemdb install-skill` so `~/.agents/skills/rubygemdb` has the final refactored `SKILL.md` and `scripts/gem.py`.

**Verification:**
```bash
cd /home/b08x/WorkspaceV3/rubygemdb && uv run rubygemdb install-skill
! grep -q "DEVSTRAL_API_KEY" /home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/SKILL.md
grep -q "rubygemdb-mcp" ~/.agents/skills/rubygemdb/SKILL.md
grep -q "OPENROUTER_API_KEY" ~/.agents/skills/rubygemdb/SKILL.md
```

---

### 5. Add Automated Pytest Suite (`tests/test_skill_gem_cli.py`) & Run Quality Checks
**Files touched:**
- `/home/b08x/WorkspaceV3/rubygemdb/tests/test_skill_gem_cli.py`

**Changes:**
- Load repo-local `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/scripts/gem.py` and `.skills/rubygemdb/install.py` directly via `Path(__file__).parent.parent / ".skills" / "rubygemdb"` (100% self-contained inside the repository).
- Test installer deployment (`install_skill` and `rubygemdb install-skill`) in both copy and `--link` modes against a `tmp_path` target directory.
- Test every `gem.py` subcommand and new flag against isolated `tmp_path` SQLite databases (`test.sqlite` and `sessions.sqlite`):
  - `search` with `--category`, `--phase`, `--expand`, `--rerank`, and `--json`
  - `info` returning `sub_categories` and `genai_uses`
  - `stack` forwarding `--max-gems`
  - `categories` listing all 21 categories with `sub_categories` and single-slug detail with facet examples
  - `prefer`, `unprefer`, `preferred`, `stats`
  - `graphrag build` and `graphrag query` (with mocked `GraphRAGService` verifying `[C1]` `citations`, `grounded_context`, and `SessionStorage` persistence)
  - `sessions list`, `sessions show`, `sessions export`, and `sessions delete`
- Test `.skills/rubygemdb/SKILL.md` synchronization:
  - Assert `DEVSTRAL_API_KEY` is absent and `OPENROUTER_API_KEY` is present
  - Assert all 11 MCP tool names, both MCP resource URIs, both MCP prompt names, and operator CLI subcommands (`import-stars`, `prune`, `reclassify`, `cache`, `install-skill`) are documented in `SKILL.md`.

**Verification:**
```bash
cd /home/b08x/WorkspaceV3/rubygemdb && uv run pytest tests/test_skill_gem_cli.py -v
cd /home/b08x/WorkspaceV3/rubygemdb && uv run pytest
cd /home/b08x/WorkspaceV3/rubygemdb && uv run ruff check src/rubygemdb/ tests/test_skill_gem_cli.py .skills/rubygemdb/
```

---

## Risks & Open Questions

- **Lazy Import of Heavy Dependencies in `gem.py`**: `GraphRAGService`, `QueryExpander`, and `CrossEncoderReranker` must be imported lazily inside their respective subcommand handlers so fast offline commands (`search`, `info`, `categories`, `stats`) retain sub-100ms startup.
- **Idempotent Skill Deployment**: `install_skill` must cleanly overwrite existing files or symlinks at `<target_dir>/rubygemdb` so repeat runs of `uv run rubygemdb install-skill` never fail with `FileExistsError`.
