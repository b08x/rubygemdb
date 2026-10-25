# Facts

- A reusable CLI multi-selector in `src/rubygemdb/ui/selector.py` formats prune candidates with gem names, reasons (e.g. stale release duration, name normalization overlap), and details.
- The multi-selector uses `gum choose --no-limit` when `gum` is available in PATH and stdin is a TTY, parsing selected gem names.
- The multi-selector provides a native Textual `SelectionList` interface when `gum` is not available or when the Textual backend is selected, allowing checkbox navigation with Space, select-all, and confirm/cancel bindings.
- A new CLI command `rubygemdb prune` is available with options `--all`, `--select`, `--dry-run`, `--prune-list`, `--output-list`, and `--backend`.
- Running `rubygemdb prune` without arguments on an interactive terminal presents the multi-selector for all identified stale and overlap candidates.
- Passing `--output-list <path>` saves the selected gem names to a plain text file compatible with `--prune-list`.
- The `rubygemdb reclassify` command supports a `--select-prune` flag that launches the multi-selector for candidates before wiping and rebuilding classified data.
- The `rubygemdb import-stars` command supports a `--select-prune` flag that launches the multi-selector for audit candidates after star discovery and resolution.
- Running with `--dry-run` displays which gems were selected for pruning and guarantees zero deletions from the SQLite inventory and classification tables.
- Automated tests verify candidate formatting, `gum choose` invocation and output parsing, Textual selection logic, non-interactive fallbacks, CLI argument handling, and prune execution.
