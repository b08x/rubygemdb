# Implementation Plan: Refined Gem Stack Selection & Second-Pass Curation

## Solution Approach
Refactor `StackService` (`src/rubygemdb/services/stack.py`) from an unconditional dump of all 21 category seeds and all preferred gems into a two-stage hierarchical retrieval and refinement pipeline. 

- **Stage 1 (Relevance-Gated Candidate Pool)**: Retrieve vector/semantic candidates from `txtai`. Identify the active, query-relevant taxonomy categories. Gate base seeds and user-preferred gems so only those belonging to relevant categories enter the candidate pool.
- **Stage 2 (Second-Pass Refinement & Contextual Role Assignment)**: Curate the candidate pool down to a compact, cohesive stack of 5–12 gems (capped by `--max-gems`, default 10). When an LLM API key is present, use `LLMService` to select the optimal stack and generate workflow-specific role descriptions. When offline, fall back to cross-encoder / vector score thresholding with category density limits (max 1–3 gems per category).
- **Stage 3 (Layered Manifest & Clean Gemfile)**: Assemble the manifest into active stack layers (omitting empty layers/categories) and render a clean Gemfile with contextual role annotations.

---

## Ordered Steps

### Step 1: Category Relevance Gating & Candidate Pool Gathering
- **Files**: `src/rubygemdb/services/stack.py`
- Identify relevant categories from the top semantic search hits and query keywords.
- Gate `base_gems` and `storage.get_preferred()` so only gems belonging to active, query-relevant categories enter the candidate pool.
- Never add seeds of irrelevant categories (e.g. `static_site_generation`, `media_processing`) to the stack unless the query asks for them.
- *Verification*: Unit tests verifying that a query like "tui nlp text processing" does not pull in `jekyll`, `prawn`, or `ruby-vips`.

### Step 2: Second-Pass Selection Engine in `StackService` & `LLMService`
- **Files**: `src/rubygemdb/services/stack.py`, `src/rubygemdb/services/llm.py`
- Add `LLMService.refine_stack_selection(query, candidates, max_gems)` to curate the candidate pool, eliminate redundant gems, and generate task-specific role descriptions.
- Implement `StackService._offline_refine_candidates(...)` as a fallback using semantic scores and category diversity caps (max 1–3 gems per category, total <= `max_gems`).
- *Verification*: Test both online LLM refinement (mocked) and offline fallback refinement with score thresholding.

### Step 3: CLI & MCP Integration (`--max-gems` Flag)
- **Files**: `src/rubygemdb/cli.py`, `src/rubygemdb/mcp_server.py`
- Add `--max-gems` CLI argument to `stack_parser` (default: 10, type: int).
- Pass `max_gems` into `StackService.build_stack(query, max_gems=...)`.
- Update MCP server `compose_stack` tool to accept optional `max_gems: int = 10`.
- *Verification*: Run `rubygemdb stack "tui nlp text processing" --max-gems 8` and verify output size.

### Step 4: Manifest & Gemfile Layer Rendering Updates
- **Files**: `src/rubygemdb/services/stack.py`
- Ensure `build_stack` only includes layers that have at least one curated gem.
- Render Gemfile with the task-specific contextual role comments generated in Step 2.
- *Verification*: Verify `StackService.render_gemfile` and `StackService.render_manifest_yaml`.

### Step 5: Test Suite Updates & End-to-End Verification
- **Files**: `tests/test_stack.py`, `tests/test_mcp.py`
- Update existing tests in `test_stack.py` to reflect relevance-gated seeds.
- Add comprehensive tests for second-pass refinement, category density capping, and offline fallback.
- Run `ruff check`, `mypy src/rubygemdb/`, and `pytest`.

---

## Risks and Mitigations
- **Risk**: Over-filtering could result in an empty stack for very obscure queries.
  - *Mitigation*: Ensure fallback always preserves the top scoring candidates even if confidence is low, and universally includes essential runtime baseline if applicable.
- **Risk**: LLM latency on `stack` command execution.
  - *Mitigation*: The candidate pool sent to the LLM is pre-filtered and compact (~15–20 candidates), and prompt responses are cached via `LLMService` cache.
