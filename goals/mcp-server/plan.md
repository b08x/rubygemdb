# Implementation Plan: Model Context Protocol (MCP) Server for RubyGemDB

## Solution Approach

Implement a native Model Context Protocol (MCP) server for RubyGemDB using Python's official `FastMCP` (`mcp.server.fastmcp`). 
The server will run as a CLI tool (`rubygemdb-mcp`) communicating over `stdio` by default (enabling frictionless agent child-process spawning for tools like Claude Code, Antigravity, and Cursor). It will also support `--daemon start|stop|status` to run detached in the background over Server-Sent Events (SSE). 

All tools will operate strictly in read-only mode, leveraging SQLite FTS5 for sub-millisecond search latency while deferring heavy vector embeddings (`txtai`) to avoid startup handshake timeouts.

---

## Ordered Steps & Impacted Systems

### Step 1: Dependencies & Entrypoint Registration
- **Files touched:**
  - `pyproject.toml`
- **Actions:**
  - Explicitly add `"mcp>=1.29.0"` to `dependencies`.
  - Register `rubygemdb-mcp = "rubygemdb.mcp_server:main"` under `[project.scripts]`.
- **Verification:**
  - `uv sync`
  - Verify entrypoint availability in virtual environment: `uv run rubygemdb-mcp --help`.

### Step 2: MCP Server Core, Tools, Resources & Prompts
- **Files touched:**
  - `src/rubygemdb/mcp_server.py` (new)
- **Actions:**
  - Instantiate `FastMCP(name="rubygemdb", description="Ruby gem analysis and architectural classification")`.
  - **Tools:**
    - `search_gems(query: str, category: Optional[str] = None, phase: Optional[str] = None, limit: int = 10)`: Call `hybrid_search` using fast SQLite FTS5, with lazy txtai embedding fallback if available; filter by category/phase if supplied.
    - `get_gem(name: str)`: Query `ClassifiedGem` from `SQLiteStorage` / database, returning category, layer, sub-categories, agent description, dependencies, signals, and risks.
    - `compose_stack(query: str)`: Call `StackService.build_stack` returning structured layered manifest (substrate -> quality) and Gemfile snippet.
    - `list_categories()`: Return 21 categories, layers, descriptions, and base gems from `CATEGORIES`.
  - **Resources:**
    - `rubygemdb://taxonomy`: Markdown reference of all 21 categories, layer associations, and sub-category vocabularies.
    - `rubygemdb://stack-seeds`: Markdown reference of curated base gems grouped by architectural layer.
  - **Prompts:**
    - `design-ruby-stack(project_description: str)`: Prompt guiding coding agents to explore categories, search candidates layer-by-layer, and output a validated stack.
- **Verification:**
  - Python tests calling each tool, reading resources, and rendering prompts.

### Step 3: CLI & Daemon Lifecycle Management
- **Files touched:**
  - `src/rubygemdb/mcp_server.py`
- **Actions:**
  - Implement CLI argument parser:
    - Default (no args): Run stdio transport (`mcp.run(transport="stdio")`).
    - `--sse` / `--transport sse`: Run SSE transport in foreground (`mcp.run(transport="sse", port=..., host=...)`).
    - `--daemon start`: Spawn background process detached via `subprocess.Popen(..., start_new_session=True)`, redirecting output to `data/mcp.log` and saving PID to `data/mcp.pid`.
    - `--daemon stop`: Read `data/mcp.pid`, issue SIGTERM, poll for clean shutdown, remove PID file.
    - `--daemon status`: Check if process in `data/mcp.pid` is alive, reporting status, PID, port, and log location.
- **Verification:**
  - Run `uv run rubygemdb-mcp --daemon start --port 8011`
  - Run `uv run rubygemdb-mcp --daemon status` -> reports running
  - Run `uv run rubygemdb-mcp --daemon stop` -> reports stopped and cleans up `data/mcp.pid`

### Step 4: Automated Test Suite
- **Files touched:**
  - `tests/test_mcp.py` (new)
- **Actions:**
  - Test tool function execution with mock and real SQLite storage.
  - Test resource handlers and prompt template generation.
  - Test daemon CLI lifecycle routines (`start`, `status`, `stop`).
  - Test that all database operations remain read-only.
- **Verification:**
  - `uv run pytest tests/test_mcp.py`
  - `uv run pytest tests/`

### Step 5: Code Quality, Linting & Type Checking
- **Files touched:**
  - `src/rubygemdb/mcp_server.py`
  - `tests/test_mcp.py`
- **Actions:**
  - Run Ruff and Mypy on all touched files.
- **Verification:**
  - `uv run ruff check src/rubygemdb/mcp_server.py tests/test_mcp.py`
  - `uv run mypy src/rubygemdb/mcp_server.py`

### Step 6: Documentation & Agent Configuration
- **Files touched:**
  - `README.md`
  - `GEMINI.md`
  - `AGENTS.md`
- **Actions:**
  - Add `rubygemdb-mcp` documentation and usage instructions.
  - Provide ready-to-copy MCP server configuration snippets for Claude Code (`claude.json`), Antigravity (`mcp_config.json`), and general SSE setups.
- **Verification:**
  - Manual doc review and markdown linting.

---

## Risks & Open Questions

1. **Process isolation & port collisions for SSE daemon**:
   - Default port will be 8000; configurable via `--port`.
   - Before starting `--daemon start`, the script will verify if the port is in use and error early with a clear message.
2. **SQLite multi-process concurrency**:
   - Because MCP tools are strictly read-only, opening connections with `uri=True` or `timeout=5.0` ensures no database contention if the TUI or batch processor is active.
