# Goal: GraphRAG Session Tracking (TUI & MCP)

Implement persistent, cross-interface GraphRAG session tracking for RubyGemDB across both the Textual TUI (`AgentChatTab`) and the MCP server (`rubygemdb-mcp`). Session metadata, conversation turns, AST node citations, and container execution traces are persisted in a dedicated SQLite database (`data/sessions.sqlite`), keeping the gem catalog strictly read-only, while conversational turns are indexed into txtai embedding memory using the `RecursiveParentChildChunker` engine.

## Shared Understanding
All accepted behavior, UI/UX expectations, and verification standards are documented in [facts.md](file:///home/b08x/WorkspaceV3/rubygemdb/goals/graphrag-session-tracking/facts.md).

## Execution Plan
The step-by-step implementation order, touched files, verification checks, and risk mitigations are specified in [plan.md](file:///home/b08x/WorkspaceV3/rubygemdb/goals/graphrag-session-tracking/plan.md).

## Done Condition
- Dedicated `SessionStorage` service managing `data/sessions.sqlite` with full CRUD support for sessions, messages, and traces.
- `TxtaiAgent` binds active sessions, indexes conversation turns into txtai using `RecursiveParentChildChunker` tagged with `session_id`, and restores conversation history.
- MCP server exposes `list_rag_sessions`, `get_rag_session`, `resume_rag_session`, `export_rag_session_transcript`, and `delete_rag_session`, with optional `session_id` support in `query_gem_graphrag` and `build_gem_graphrag`.
- Textual TUI `AgentChatTab` supports auto-resuming the latest session, switching sessions via a session picker, restoring active gems and intention, starting fresh sessions, and deleting sessions.
- Comprehensive automated test suite in `tests/test_session_tracking.py` passes 100%, and `uv run ruff check` / `uv run mypy` report zero errors.
