# Implementation Plan: Ephemeral Gem GraphRAG Pipeline & Advisor Retirement

## Solution Approach

Replace the obsolete card-and-phase-walking Advisor service with an on-demand, containerized GraphRAG pipeline. 
When given a set of selected gems and a project intention:
1. Source repositories are resolved from database metadata/API and cached via shallow clones (`--depth 1`) with a `gem unpack` fallback.
2. A lightweight container runtime (Podman first, Docker fallback) runs an ephemeral image (`rubygemdb-indexer:latest`) containing Ruby, `rubydex`, `decode`, and `bake`.
3. The container indexes all mounted gem repositories using `Rubydex::Graph` (AST, class hierarchy, method calls, constant references) and `Decode::Index` (structured docstrings, `@parameter`, `@returns`, and `@example` code blocks), writing a normalized JSON graph artifact to the host.
4. Host Python ingests the graph artifact, populates an ephemeral `txtai` vector index for semantic retrieval, and executes two-stage GraphRAG (semantic vector match + 1-hop graph neighborhood expansion).
5. Grounded context (signatures, parameter types, real code examples, call relations) is fed into the LLM chat agent in the Textual TUI and exposed through new Model Context Protocol (MCP) tools.

---

## Ordered Implementation Steps

### Step 1: Retire the Advisor Service & Clean Phase Model
- **Files Touched**:
  - Delete `src/rubygemdb/services/advisor.py`
  - Delete `src/rubygemdb/services/advisor_export.py`
  - Delete `tests/test_advisor.py`
  - Edit `src/rubygemdb/models/phases.py` (clean docstrings and comments referencing advisor card walk)
  - Edit `src/rubygemdb/agent.py` (strip `AdvisorSession`, `_advisor`, `advise()`, `reset_advisor()`, `_card_llm`, `_intent_llm`, `_reason_llm`)
- **Verification**:
  - `python -c "import rubygemdb.services; import rubygemdb.agent; print('cleaned')"`
  - `uv run pytest tests/test_phases.py tests/test_hybrid_search.py`
  - Verify no import errors in test suite.

### Step 2: Container Definition & Ruby Graph Extraction Script
- **Files Touched**:
  - Create `docker/indexer.Dockerfile` (Ruby 3.3-slim, git, build-essential, rubydex, decode, bake)
  - Create `docker/extract_graph.rb` (CLI script that accepts paths to gem directories, runs `Rubydex::Graph` + `Decode::Index`, and writes normalized `graph.json`)
  - Create `docker/README.md` (documentation on image building and usage)
- **Verification**:
  - Validate syntax of `extract_graph.rb` with `ruby -c docker/extract_graph.rb` (if local ruby exists) or automated fixture tests.
  - Test container image build command: `podman build -t rubygemdb-indexer:latest -f docker/indexer.Dockerfile docker/`.

### Step 3: Gem Source Resolution, Caching & Fallback Fetcher
- **Files Touched**:
  - Create `src/rubygemdb/services/gem_fetcher.py` (`GemFetcher` class):
    - Reads gem metadata (`source_code_uri`, `homepage`) from SQLite / RubyGems API.
    - Manages shallow clones in `data/cache/gem_repos/<gem_name>` with `--depth 1`.
    - Handles `.gem unpack` fallback when git repositories are unavailable or fail to clone.
  - Create `tests/test_gem_fetcher.py`
- **Verification**:
  - `uv run pytest tests/test_gem_fetcher.py`
  - Run fetcher against a sample gem (e.g. `drydock` or `thor`) verifying repository caching.

### Step 4: Python Container Runner Service
- **Files Touched**:
  - Create `src/rubygemdb/services/container_runner.py` (`ContainerRunner` class):
    - Detects container runtime (`podman` prioritized, fallback to `docker`).
    - Checks for `rubygemdb-indexer:latest` image and auto-builds if missing.
    - Executes container with read-only gem mounts and a mounted output directory.
    - Parses and validates output `graph.json`.
  - Create `tests/test_container_runner.py`
- **Verification**:
  - `uv run pytest tests/test_container_runner.py`
  - Verify container execution handles volume mounting, exit codes, and error logging cleanly.

### Step 5: Graph Ingestion, Ephemeral txtai Vector Store & GraphRAG Engine
- **Files Touched**:
  - Create `src/rubygemdb/services/graphrag.py` (`GraphRAGService` class):
    - Ingests `graph.json` nodes and edges into an in-memory graph index.
    - Builds ephemeral `txtai` vector embeddings for symbols, docstrings, and decode code examples.
    - Caches index by gem set hash (`data/cache/graphrag/<hash>/`).
    - Implements two-stage retrieval: semantic top-K vector search + 1-hop graph neighborhood expansion (callers, callees, inheritance, attached decode examples).
    - Assembles prompt context with intention, exact signatures, types, and examples.
  - Create `tests/test_graphrag.py`
- **Verification**:
  - `uv run pytest tests/test_graphrag.py` with mock graph fixtures.

### Step 6: Agent Refactoring & Grounded Chat Integration
- **Files Touched**:
  - Edit `src/rubygemdb/agent.py`:
    - Wire `GraphRAGService` into `TxtaiAgent`.
    - Implement `set_graphrag_context(gems: list[str], intention: str, progress_callback: Optional[Callable[[str], None]] = None)`.
    - Implement `chat(query: str, project_context: Optional[str] = None) -> str`.
    - Provide `export_transcript(out_dir: Path) -> tuple[Path, Path]`.
  - Create `tests/test_agent_graphrag.py`
- **Verification**:
  - `uv run pytest tests/test_agent_graphrag.py`

### Step 7: TUI Chat Tab Transformation
- **Files Touched**:
  - Edit `src/rubygemdb/ui/tui.py`:
    - In `AgentChatTab`:
      - Remove advisor stage labels, slot search loops, and advisor session calls.
      - Add `SelectedGemsBar` with badges of gems selected from Explorer tab.
      - Add `ProjectIntentionInput` (`#intention-input`) for project context.
      - Add `Build Context` button (`#build-graphrag-btn`) triggering async container run.
      - Stream live step-by-step progress into `#trace-log`.
      - Wire `#chat-input` to `agent.chat()`.
      - Repurpose `#export-chat-btn` to export markdown chat transcript with active intention, indexed gems, and cited symbols.
- **Verification**:
  - Run headless/dry-run checks on `tui.py`.
  - Test UI components and event handlers.

### Step 8: MCP Server Tools & Prompts
- **Files Touched**:
  - Edit `src/rubygemdb/mcp_server.py`:
    - Add `@mcp.tool() def query_gem_graphrag(gems: list[str], query: str, intention: Optional[str] = None) -> Dict[str, Any]` (auto-indexes if needed).
    - Add `@mcp.tool() def build_gem_graphrag(gems: list[str], intention: str) -> Dict[str, Any]` (explicit pre-warm tool).
    - Add `@mcp.prompt() def gem_architect_rag(gems: list[str], intention: str, task: str) -> str`.
  - Create `tests/test_mcp_graphrag.py`
- **Verification**:
  - `uv run pytest tests/test_mcp_graphrag.py`

### Step 9: Linting, Type Checking & Documentation Updates
- **Files Touched**:
  - Edit `AGENTS.md`, `GEMINI.md`, `README.md` (remove advisor references, document container GraphRAG pipeline and MCP tools).
- **Verification**:
  - `uv run ruff check src/rubygemdb/`
  - `uv run mypy src/rubygemdb/`
  - `uv run pytest tests/`

---

## Risks and Mitigation

1. **Host Container Permission / SELinux**:
   - *Risk*: Mounting host volumes into rootless Podman containers on Fedora/RHEL may hit SELinux permission denials.
   - *Mitigation*: Use appropriate volume flags (`:ro,z` or `:rw,z` when SELinux is enforcing) and check container runner permissions.
2. **Missing Git Repositories / Monorepos**:
   - *Risk*: Some gems reside in subdirectories of large monorepos (e.g. `rails/rails`), or git URIs are 404.
   - *Mitigation*: Fall back to `gem unpack` to always index the exact gem code distributed on RubyGems.
3. **Container Build Latency on First Run**:
   - *Risk*: First container build might take 1-2 minutes depending on network.
   - *Mitigation*: Provide clear progress logging in TUI trace and CLI output; image build is a one-time operation cached locally.
