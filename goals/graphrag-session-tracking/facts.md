# Facts

- Session tracking data is stored in a dedicated SQLite database located at data/sessions.sqlite (configurable via settings.session_db_file), keeping the gem catalog database isolated and read-only for MCP.
- The session database defines schema tables for rag_sessions (metadata, active gems, intention, timestamps), rag_messages (roles, contents, retrieved node IDs, timestamps), and rag_traces (execution steps, timestamps).
- A SessionStorage service provides robust methods to create, get, list, update, and delete sessions, append messages, and log execution traces.
- User queries and agent responses within a session are indexed into txtai embedding memory tagged with session_id, enabling semantic search over prior conversation turns.
- Session conversational memory indexed into txtai utilizes the RecursiveParentChildChunker engine, ensuring parent-child chunk linkage and character offset preservation.
- MCP tools query_gem_graphrag and build_gem_graphrag accept an optional session_id parameter; if omitted, a new UUID session is generated and returned in the tool response.
- Executing query_gem_graphrag with a session_id persists the user query, retrieved AST nodes, and generated response into rag_messages in the session database.
- The MCP server exposes list_rag_sessions, get_rag_session, and resume_rag_session tools for inspecting and continuing existing sessions.
- The MCP server exposes export_rag_session_transcript to export formatted markdown and JSON transcripts for any tracked session.
- The MCP server exposes delete_rag_session to remove a session, its message history, and traces from the database.
- TxtaiAgent supports binding to an active session_id, loading past conversation turns into memory context, and automatically saving new turns to SessionStorage.
- The Textual TUI AgentChatTab automatically saves conversation turns and restores the most recently active session upon opening the chat tab.
- The TUI chat interface provides a session selector dropdown to browse, inspect, and switch between previous GraphRAG sessions.
- Switching or resuming a session in the TUI restores its active gems, project intention, full message history in the chat log, and previous execution traces.
- The TUI chat interface provides a 'New Session' button that saves the current session and starts a new session with cleared inputs and a fresh session ID.
- The TUI chat interface allows deleting an existing session, removing it from the session selector and database.
