# Implementation Plan: GraphRAG Session Tracking (TUI & MCP)

## Solution Approach
Add persistent, cross-interface GraphRAG session tracking to RubyGemDB. All session metadata, messages, traces, and node citations are stored in a dedicated SQLite database (`data/sessions.sqlite`, managed via `SessionStorage`), preserving the strict read-only integrity of the gem catalog database (`data/rubygemdb.sqlite`) for MCP. Conversational turns are chunked with `RecursiveParentChildChunker` and indexed into txtai tagged with `session_id`. Both the Textual TUI (`AgentChatTab`) and MCP server tools (`rubygemdb-mcp`) share this session store for seamless resumption, inspection, and export.

---

## Ordered Steps & Touchpoints

### Step 1: Configuration & Models
- **Files**:
  - `src/rubygemdb/core/config.py`
  - `src/rubygemdb/models/session.py` (New)
- **Changes**:
  - Add `session_db_file: Path = data_dir / "sessions.sqlite"` to `Settings`.
  - Create Pydantic models:
    - `RAGSession`: `session_id`, `title`, `created_at`, `updated_at`, `active_gems`, `intention`, `status`, `metadata`.
    - `RAGMessage`: `id`, `session_id`, `role` (`user`|`agent`|`system`|`error`), `content`, `timestamp`, `retrieved_nodes`, `trace_step`.
    - `RAGTrace`: `id`, `session_id`, `trace_text`, `timestamp`.
    - `SessionTranscript`: markdown and JSON serializable models.
- **Verification**:
  - `uv run python -c "from rubygemdb.core.config import settings; from rubygemdb.models.session import RAGSession; print(settings.session_db_file)"`

---

### Step 2: Dedicated Session Storage Service
- **Files**:
  - `src/rubygemdb/storage/session_storage.py` (New)
- **Changes**:
  - Implement `SessionStorage` with thread-safe SQLite connection handling for `sessions.sqlite`.
  - Schema initialization for `rag_sessions`, `rag_messages`, and `rag_traces` tables with foreign keys and indexes.
  - Implement CRUD APIs:
    - `create_session(session_id=None, title="", active_gems=[], intention="", metadata={}) -> RAGSession`
    - `get_session(session_id) -> Optional[RAGSession]`
    - `list_sessions(limit=50) -> list[RAGSession]`
    - `update_session(session_id, ...) -> Optional[RAGSession]`
    - `delete_session(session_id) -> bool`
    - `append_message(session_id, role, content, retrieved_nodes=None, trace_step=None) -> RAGMessage`
    - `get_messages(session_id) -> list[RAGMessage]`
    - `append_trace(session_id, trace_text) -> RAGTrace`
    - `get_traces(session_id) -> list[RAGTrace]`
    - `get_latest_session() -> Optional[RAGSession]`
    - `export_transcript(session_id, format="markdown"|"json") -> tuple[str, dict]`
- **Verification**:
  - Unit tests for all CRUD operations, foreign key cascades, and transcript generation.

---

### Step 3: TxtaiAgent Session Integration & Embeddings Indexing
- **Files**:
  - `src/rubygemdb/agent.py`
- **Changes**:
  - Inject `SessionStorage` into `TxtaiAgent`.
  - Add `bind_session(session_id=None, create_if_missing=True) -> RAGSession` to set active session and reload conversation turns into `_chat_history`.
  - Update `chat(query, project_context=None)`:
    - Auto-bind session if none active.
    - Persist user query to `rag_messages`.
    - Chunk query and agent response with `RecursiveParentChildChunker` and index into txtai with `session_id` and `doc_type="session_turn"`.
    - Persist agent response and retrieved node IDs to `rag_messages`.
  - Update `set_graphrag_context`:
    - Record active gems and intention in active session.
    - Log progress callback traces into `rag_traces`.
  - Update `export_transcript` to optionally route through `SessionStorage`.
- **Verification**:
  - Automated tests verifying session binding, turn persistence, and parent-child chunked embedding retrieval under `session_id`.

---

### Step 4: MCP Server Session Tools & Protocol
- **Files**:
  - `src/rubygemdb/mcp_server.py`
- **Changes**:
  - Add session management tools:
    - `list_rag_sessions(limit: int = 20) -> List[Dict[str, Any]]`
    - `get_rag_session(session_id: str) -> Dict[str, Any]`
    - `resume_rag_session(session_id: str) -> Dict[str, Any]`
    - `export_rag_session_transcript(session_id: str, format: str = "markdown") -> Dict[str, Any]`
    - `delete_rag_session(session_id: str) -> Dict[str, Any]`
  - Update `query_gem_graphrag`:
    - Accept `session_id: Optional[str] = None`.
    - Auto-create session if omitted, record user query and answer in `rag_messages`, and return `session_id` in response.
  - Update `build_gem_graphrag`:
    - Accept `session_id: Optional[str] = None`.
    - Update session gems, intention, and status in `rag_sessions`.
- **Verification**:
  - Test MCP tool invocations directly via FastMCP test client / pytest.

---

### Step 5: Textual TUI Chat Tab Session Management
- **Files**:
  - `src/rubygemdb/ui/tui.py`
- **Changes**:
  - In `AgentChatTab`:
    - Add Session Bar above chat with:
      - `Select` dropdown for switching sessions.
      - "New Session" button.
      - "Delete Session" button.
    - `on_mount`: fetch latest session from `SessionStorage` (or create new if none) and restore:
      - Active gems and badges.
      - Intention input value.
      - Formatted chat history in `#chat-log`.
      - Previous container/GraphRAG traces in `#trace-log`.
    - Handle session switching from the dropdown: flush active state, load target session, re-bind agent.
    - "New Session" button handler: save current, create fresh session, clear UI inputs, reset agent session binding.
    - "Delete Session" button handler: delete from `SessionStorage`, refresh dropdown, switch to previous or create fresh.
    - Sync gems selected in Explorer tab directly to active session.
- **Verification**:
  - Textual async unit test testing mount, session switching, new session creation, and message restoration.

---

### Step 6: Test Suite & Quality Verification
- **Files**:
  - `tests/test_session_tracking.py` (New)
  - `tests/test_mcp_server.py`
- **Changes**:
  - Automated tests covering all 16 facts in `goals/graphrag-session-tracking/facts.md`.
  - Run full suite: `uv run pytest tests/`.
  - Linting & type checking: `uv run ruff check` and `uv run mypy`.
- **Verification**:
  - `uv run pytest tests/test_session_tracking.py -v` passes 100%.
  - `uv run ruff check src/rubygemdb/` is clean.

---

## Risks & Mitigations
- **MCP Concurrency & SQLite Locks**: MCP runs as an async server with possible concurrent requests.
  - *Mitigation*: Enable SQLite WAL mode (`PRAGMA journal_mode=WAL;`) and a busy timeout in `SessionStorage`.
- **Large Trace / Chat Bloat**: Long-running container extraction can generate dozens of trace lines.
  - *Mitigation*: Limit trace query fetching in the TUI to the last 200 events or paginate, keeping UI responsive.
- **Backwards Compatibility**: Existing installations without `sessions.sqlite`.
  - *Mitigation*: Auto-create `data/sessions.sqlite` on first access with `IF NOT EXISTS` table definitions.
