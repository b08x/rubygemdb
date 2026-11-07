# Goal: Refactor `rubygemdb` Skill, Move to `.skills/rubygemdb`, & Add Installer

## Articulated Goal
Migrate the `rubygemdb` skill from `~/.syncopated/skills/rubygemdb` into `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb` (`SKILL.md`, `scripts/gem.py`, and `install.py`), add a CLI installer (`uv run rubygemdb install-skill` / `python .skills/rubygemdb/install.py`) that deploys the skill to `~/.agents/skills/rubygemdb`, and refactor both `SKILL.md` and `scripts/gem.py` to match the latest `rubygemdb` codebase (3-tier MCP/script/CLI routing, containerized GraphRAG with `[C1]` grounded citations, session management, sub-category facets, local GGUF query expansion/reranking, and updated environment variables).

## Shared Understanding
- **Facts**: See [`facts.md`](file:///home/b08x/WorkspaceV3/rubygemdb/goals/refactor-rubygemdb-skill/facts.md) for the agreed-upon outcomes and verifiable behaviors.

## Execution Plan
- **Plan**: See [`plan.md`](file:///home/b08x/WorkspaceV3/rubygemdb/goals/refactor-rubygemdb-skill/plan.md) for the ordered implementation steps, files touched, and verification checks.

## Done Condition
1. The canonical skill resides at `/home/b08x/WorkspaceV3/rubygemdb/.skills/rubygemdb/` (`SKILL.md`, `scripts/gem.py`, `install.py`), `~/.syncopated/skills/rubygemdb` is cleaned up, and `uv run rubygemdb install-skill` deploys the skill to `~/.agents/skills/rubygemdb`.
2. `.skills/rubygemdb/scripts/gem.py` supports `--category`, `--expand`, and `--rerank` on `search`, `--max-gems` on `stack`, `sub_categories` (80 facets + examples) and `genai_uses` on `categories` and `info`, plus `graphrag` (`build`/`query`) and `sessions` (`list`/`show`/`export`/`delete`) subcommands.
3. `.skills/rubygemdb/SKILL.md` documents the 3-tier routing model (`rubygemdb-mcp` first, `gem.py` fallback + single-gem mutations, `uv run rubygemdb` operator CLI), all 11 MCP tools, resources, prompts, SSE daemon lifecycle, containerized GraphRAG `[C1]` citation rules, operator CLI commands, and current environment variables (`OPENROUTER_API_KEY` replacing `DEVSTRAL_API_KEY`).
4. `tests/test_skill_gem_cli.py` passes alongside the full `pytest` suite and `ruff check` with zero errors.
