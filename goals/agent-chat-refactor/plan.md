# Plan: Agent Chat Refactor

## Solution approach

Remove Trackboi distillation, then replace the free-form "architect" chat with a **phase-walking advisor** whose state lives in Python, not in the LLM's memory.

- A new `phases.py` defines four ordered build phases (skeleton, input, processing, output) and maps each of the 21 categories to one phase.
- A new hybrid search merges txtai vector results and SQLite FTS5 keyword results with reciprocal rank fusion (RRF), filtered by phase.
- A new `AdvisorSession` is a small state machine: brief → capabilities → one phase at a time → user action (accept / swap / ask / skip / back) → next phase. It records confirmed picks.
- `TxtaiAgent.advise()` exposes the session to the TUI. The existing `ToolCallingAgent` (with Context7 and codebase-memory tools) is kept only to answer the "ask a follow-up about a gem" action.
- The export is built from session state by a pure function: markdown + JSON.

Because the session is plain Python, "do not show the next phase until the current one is resolved" and "the sliding window never drops picks" are structural, and can be tested without an LLM (LLM calls are injected callables).

## Steps

### 1. Phase taxonomy
- **Files:** new `src/rubygemdb/models/phases.py`; `tests/test_phases.py`.
- Define `PHASES = ["skeleton", "input", "processing", "output"]` and `PHASE_BY_CATEGORY: dict[str, str]`. Proposed mapping (adjustable in this one table):
  - skeleton: cli_libraries, web_frameworks, gui_desktop, servers_concurrency, core_extensions, developer_tools, testing_qa, code_quality_typing
  - input: http_networking, document_parsing, native_bindings, security_auth, mcp_tooling
  - processing: ai_llm, text_search, media_processing, runtime_validation, background_jobs, persistence
  - output: document_generation, static_site_generation
- Helpers: `phase_for(category_slug)`, `categories_for(phase)`.
- **Verify:** `uv run pytest tests/test_phases.py`. The test asserts every slug in `CATEGORIES` is mapped to exactly one phase, no unknown slugs are mapped, and `PHASES` order is fixed. (Facts 7, 8.)

### 2. Remove Trackboi
- **Files:** `src/rubygemdb/agent.py` (delete `get_trackboi_distiller_agent`), `src/rubygemdb/core/config.py` (delete `trackboi_distiller_model`), `src/rubygemdb/ui/tui.py` (delete `DistillSelectionScreen`, `ProjectFolderScreen`, `#distill-chat-btn`, `on_distill_chat`, the `[DISTILL_TRACKBOI` branch in `process_query`, and the distill step), `README.md` (feature bullet, mermaid node and edge, MCP table row), `INSTALL.md` (`TRACKBOI_DISTILLER_MODEL`).
- `ProjectFolderScreen` is only referenced from `on_distill_chat` (tui.py:794), so it is removed.
- `.gitignore` / `.graphifyignore` `.trackboi/` lines are ignore patterns for a local tool directory, not the feature; leave them.
- **Verify:** `grep -rIni "trackboi\|distill" src tests README.md INSTALL.md CLAUDE.md` returns nothing; add `tests/test_no_trackboi.py` that does this scan. `uv run python -c "import rubygemdb.ui.tui"` imports cleanly. (Facts 1, 2, 3.)

### 3. Hybrid search
- **Files:** new `src/rubygemdb/services/hybrid_search.py`; `src/rubygemdb/agent.py` (`_initialize_index`, `build_index`); `tests/test_hybrid_search.py`.
- Build a `gem_fts` FTS5 table (name, description, category) in `data/rubygemdb.sqlite` from `inventory` joined with `classified_gems`. Build it on every txtai rebuild, and also when the txtai index loads from disk but `gem_fts` is missing or stale (row count differs from `inventory`).
- `hybrid_search(query, phase, limit)`: run txtai `similar()` search and FTS5 `MATCH` (query tokens OR-joined and quoted to avoid FTS syntax errors), restricted to the phase's categories; fuse with RRF (`score = Σ 1/(60 + rank)`); dedupe by gem name; return name, category, layer, phase, description, source_code_uri, context7_id, fused score.
- Vector search and FTS search are separate injectable callables so tests run without txtai.
- **Verify:** `uv run pytest tests/test_hybrid_search.py` covers: a gem found only by FTS still appears; a gem found only by vector still appears; a gem found by both ranks above single-source gems; phase filter excludes other phases' categories; FTS special characters in the query do not raise; `gem_fts` exists after build. (Facts 17-20.)

### 4. Advisor session
- **Files:** new `src/rubygemdb/services/advisor.py`; `tests/test_advisor.py`.
- Dataclasses: `Capability(name, phase)`, `PhaseState(phase, candidates, picks, status)`, `AdvisorSession(brief, capabilities, phases, current, transcript)`.
- `start(brief)`: one LLM call returns JSON capabilities with a phase each. Unknown or missing phases are rejected and re-asked once. If the brief is too vague (LLM returns `clarify` with a question), return that single question and wait.
- `present(phase)`: per capability call `hybrid_search`, merge by best fused score, take top 3-5, generate a one-line reason per gem via one LLM call (fallback: first sentence of the gem description). A phase with no capabilities or no results is marked empty and the session advances automatically with a message.
- `respond(text)`: LLM classifies intent into `accept | swap(gem, with) | ask(gem, question) | skip | back(phase) | reject_all`, with a keyword fallback if the LLM is unavailable. `accept` / `skip` are the only actions that advance. `swap` replaces a pick from the candidate list. `reject_all` offers the next-ranked gems, or re-searches with the user's own words. `back` reopens an earlier phase.
- `ask` is delegated to a callable provided by `TxtaiAgent` (the tool-calling agent).
- `reset()` clears everything (for Clear Chat).
- **Verify:** `uv run pytest tests/test_advisor.py` with fake LLM and fake search covering: phases presented strictly in order; next phase withheld until accept/skip; swap, ask, skip, back; empty phase auto-advances; reject_all; vague brief returns one clarifying question; reset clears state. (Facts 6, 7, 9-16, 22, 26.)

### 5. Agent wiring
- **Files:** `src/rubygemdb/agent.py`.
- Add `TxtaiAgent.advise(message) -> str` that owns one `AdvisorSession`. Optional project context from the selector is appended to the brief and passed to `ask`.
- Delete `_execute_search_with_expansion` and the "Other Steve" prompt; `search_memory_tool` calls `hybrid_search` without phase filter. Rewrite the architect `agent_prompt` for follow-up questions only (explain a gem, compare gems), keeping Context7 and codebase-memory tools.
- `run()` keeps indexing chat memory and the sliding window; picks live in the session so trimming cannot drop them.
- **Verify:** `uv run pytest tests/test_advisor.py -k agent_memory` asserts picks survive more than 6 steps of memory trimming. `grep -n "expansion\|Other Steve" src/` returns nothing. (Facts 4, 21, 28.)

### 6. TUI chat and export
- **Files:** `src/rubygemdb/ui/tui.py`; new `src/rubygemdb/services/advisor_export.py`; `tests/test_advisor_export.py`.
- `process_query` calls `agent.advise(...)` instead of `agent.run(...)`. Replace the fake status messages with real ones ("Decomposing brief", "Searching input phase"). Show the current phase in the chat title label.
- Chat actions row becomes Export and Clear. Clear Chat also calls `session.reset()`.
- `build_export(session) -> (markdown, json_dict)`: markdown has brief, confirmed stack by phase with rationale per gem, Gemfile snippet (`gem "name"`), full transcript; if no picks, states "No stack was confirmed." Writes `chat_<ts>.md` and `chat_<ts>.json` under `output/chat_exports/`.
- **Verify:** `uv run pytest tests/test_advisor_export.py` (markdown sections, JSON shape, Gemfile lines, empty-stack case) and `tests/test_export.py` still passes. Manual: `uv run rubygemdb-tui`, open Agent Chat, enter "a CLI app that parses PDFs, chunks text with context, extracts images, tables, and code blocks", walk four phases, export, open both files. (Facts 2, 5, 23-27.)

### 7. Docs
- **Files:** `README.md`, `CLAUDE.md` (project): describe the phase-walking chat, phase table, hybrid search, export outputs; remove `llm.py`/agent descriptions that mention Trackboi if present.
- **Verify:** `grep -rIni "trackboi\|distill" README.md INSTALL.md CLAUDE.md` is empty.

### 8. Final gate
- `uv run ruff check src/rubygemdb/ && uv run mypy src/rubygemdb/ && uv run pytest tests/`

## Risks and open questions

- **Phase mapping is a judgement call.** Quality categories (testing_qa, code_quality_typing, developer_tools) are placed in "skeleton" because a four-phase walk has no quality phase. "Output" contains only two categories because CLI output gems (tty-table) live in `cli_libraries` (skeleton). The phase filter (fact 20) would hide them from the output phase; change the table or allow a capability to search the whole index if this feels wrong during manual testing.
- **Generated wiki snapshot.** `.zread/wiki/versions/2026-09-26-170441/` mentions Trackboi. It is a dated generated snapshot, so this plan does not edit it and the grep checks exclude it. Say so if you want it regenerated or edited.
- **Intent parsing depends on an LLM.** A keyword fallback covers accept/skip/back so the walk still works offline; swap/ask need the LLM.
- **FTS5 staleness.** `gem_fts` is rebuilt with the txtai index; edits to the inventory made in the TUI after indexing are not reflected until the next rebuild, same as the vector index today.
- **Decomposition quality** can only be checked manually with the real model; automated tests use fakes.
