# Implementation Plan: CLI Multi-Selector for Pruning Gems

Implement an interactive multi-selector for selecting gems to prune, supporting both `gum choose` and Textual `SelectionList`, exposing a dedicated `rubygemdb prune` CLI command, and wiring `--select-prune` into `reclassify` and `import-stars`.

---

## Solution Approach

1. **Selector Module (`src/rubygemdb/ui/selector.py`)**:
   - Provide a clean abstraction `select_gems_to_prune(candidates, backend="auto", preselected=None)`
   - Format candidates into labels indicating candidate name, reason (stale release duration or overlap collision), and detail.
   - Dual-engine architecture:
     - **Gum Backend (`_select_with_gum`)**: Pipes candidate labels with delimiter into `gum choose --no-limit --label-delimiter="||"` and parses stdout lines for selected gem names.
     - **Textual Backend (`_select_with_textual`)**: Launches a self-contained Textual modal app `PruneSelectorApp` featuring a `SelectionList` with checkbox toggle (Space), select-all (`a`), clear-all (`u`), confirm (Enter), and cancel (Esc).
     - **Auto-detection**: Prefers `gum` if installed and connected to a TTY; falls back to Textual; gracefully handles non-interactive environments.
2. **Dedicated CLI Command (`rubygemdb prune`)**:
   - Add `prune` subcommand to `cli.py` with options:
     - `--all`: Prune all candidates without selection prompt.
     - `--select`: Explicitly launch multi-selector (default in TTY).
     - `--prune-list <file>`: Prune subset from file.
     - `--output-list <file>`: Export selected gems to a prune-list file for reuse.
     - `--dry-run`: Preview selected candidates without mutating SQLite.
     - `--backend {auto,gum,textual}`: Backend choice.
3. **Integration into `reclassify` and `import-stars`**:
   - Add `--select-prune` to `reclassify` subparser; if passed (or interactively chosen), launch multi-selector for candidates.
   - Add `--select-prune` to `import-stars` subparser and `ImportStarsPipeline`; if passed and not dry-run, prompt user with multi-selector for candidates.
4. **Automated Testing (`tests/test_prune_selector.py`)**:
   - Unit tests mocking `gum choose` process execution and output parsing.
   - Unit tests testing `PruneSelectorApp` in Textual.
   - Integration tests testing CLI flags (`--select-prune`, `--dry-run`, `--all`, `--output-list`).
5. **Documentation**:
   - Update `AGENTS.md` and `GEMINI.md` with multi-selector options and the new `rubygemdb prune` command.

---

## Ordered Steps

- [x] Create goal documentation (`goals/cli-multi-select-prune/`).
- [ ] Implement `src/rubygemdb/ui/selector.py` with dual-engine multi-selector (`gum` + `Textual`).
- [ ] Implement `rubygemdb prune` subcommand in `src/rubygemdb/cli.py`.
- [ ] Wire `--select-prune` into `reclassify` in `src/rubygemdb/cli.py`.
- [ ] Wire `--select-prune` into `import-stars` in `cli.py` and `src/rubygemdb/services/import_stars_pipeline.py`.
- [ ] Write comprehensive tests in `tests/test_prune_selector.py`.
- [ ] Verify test suite 100% pass (`uv run pytest`), linting (`ruff`), and typing (`mypy`).
- [ ] Update `AGENTS.md` and `GEMINI.md`.
