# Goal: Agent Chat Refactor

## Articulated Goal

Remove the Trackboi distillation feature from RubyGemDB and rebuild the agent chat as a project-brief advisor. Given a brief (for example, a CLI app that parses PDFs, chunks text with context, and extracts images, tables, and code blocks), the agent decomposes it into capabilities and runs a hybrid search (txtai vectors + SQLite FTS5, merged with reciprocal rank fusion). It then walks four build phases (skeleton, input, processing, output) one at a time, with the user confirming each phase before the next. The confirmed stack can be exported as markdown and JSON.

## Shared Understanding

See [facts.md](facts.md) — 29 accepted facts covering Trackboi removal, brief decomposition, the phase walk and per-phase actions, hybrid search, session state, and export.

## Execution Plan

See [plan.md](plan.md) — 8 ordered steps: phase taxonomy → Trackboi removal → hybrid search → advisor session → agent wiring → TUI and export → docs → final gate. Each step lists files touched and verification commands.

## Done Condition

All 29 facts hold on the real codebase:

- `uv run ruff check src/rubygemdb/`, `uv run mypy src/rubygemdb/`, and `uv run pytest tests/` pass.
- No `trackboi` or `distill` text remains in `src/`, `tests/`, `README.md`, `INSTALL.md`, or `CLAUDE.md`.
- A manual TUI run walks a PDF-CLI brief through all four phases and writes the markdown and JSON exports.
