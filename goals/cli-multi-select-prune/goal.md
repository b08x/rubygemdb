# Goal: CLI Multi-Selector for Pruning Gems

Provide an interactive CLI multi-selector to choose specific gems to prune from the RubyGemDB catalog. The multi-selector supports both Charmbracelet's `gum choose --no-limit` (when available in PATH and running in an interactive terminal) and a terminal Textual `SelectionList` UI (built-in Python fallback), integrates into `rubygemdb reclassify` and `rubygemdb import-stars` via `--select-prune`, and adds a dedicated `rubygemdb prune` CLI command.

## Shared Understanding

The shared understanding and testable outcomes are specified in [`facts.md`](facts.md) and [`facts.meta.json`](facts.meta.json).

Key capabilities established:
- Interactive multi-selection component (`src/rubygemdb/ui/selector.py`) that presents prune candidates (stale gems, overlap prunables) with gem names, reasons, and release details.
- Dual-engine selection: uses `gum choose --no-limit` when `gum` is available in PATH, falling back seamlessly to a Textual `SelectionList` app when `gum` is unavailable or when Textual is explicitly requested.
- Dedicated `rubygemdb prune` CLI command supporting interactive multi-selection, `--all` (prune all candidates), `--prune-list` (prune approved subset from file), `--dry-run` (simulate selection/pruning without writes), and `--output-list` (save selected gems to text file for reuse).
- Integration into `rubygemdb reclassify`: `--select-prune` flag and interactive multi-select option before catalog rebuilding.
- Integration into `rubygemdb import-stars`: `--select-prune` flag and pipeline support during star import audit.
- Automated tests covering the selector engines, CLI argument parsing, interactive selection mocking, dry-run safety, and database pruning.

## Execution Plan

The step-by-step implementation strategy and verification gates are detailed in [`plan.md`](plan.md).

## Done Condition

1. `src/rubygemdb/ui/selector.py` provides `select_gems_to_prune()` supporting both `gum choose` and Textual `SelectionList` with candidate details.
2. A new `rubygemdb prune` CLI subcommand is fully functional with `--all`, `--select`, `--dry-run`, `--prune-list`, `--output-list`, and `--backend`.
3. `rubygemdb reclassify` and `rubygemdb import-stars` support `--select-prune` to filter prune targets through the multi-selector.
4. When in `--dry-run` mode, multi-selected gems are reported without deleting from SQLite.
5. All automated unit and integration tests in `tests/test_prune_selector.py` pass, and the existing test suite passes 100% (`uv run pytest`).
