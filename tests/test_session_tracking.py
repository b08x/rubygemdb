"""Automated test suite for GraphRAG session tracking across SessionStorage, TxtaiAgent, MCP tools, and TUI."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, RichLog

from rubygemdb.agent import TxtaiAgent
from rubygemdb.mcp_server import mcp
from rubygemdb.storage.session_storage import SessionStorage
from rubygemdb.ui.tui import AgentChatTab


# --- Fixtures ---


@pytest.fixture
def session_storage(tmp_path: Path) -> SessionStorage:
    """Isolated SessionStorage instance backed by temporary SQLite DB."""
    db_file = tmp_path / "test_sessions.sqlite"
    return SessionStorage(db_path=db_file)


@pytest.fixture
def mock_agent_with_storage(tmp_path: Path, session_storage: SessionStorage) -> TxtaiAgent:
    """TxtaiAgent with patched index initialization and isolated session storage."""
    db_file = tmp_path / "inventory.sqlite"
    txtai_dir = tmp_path / "txtai"
    with patch.object(TxtaiAgent, "_initialize_index"):
        agent = TxtaiAgent(
            db_path=str(db_file),
            txtai_dir=str(txtai_dir),
            session_storage=session_storage,
        )
        return agent


# --- 1. Storage & Schema Tests ---


def test_session_storage_schema_and_fk_cascade(session_storage: SessionStorage):
    """Verify tables exist, WAL mode is active, and foreign key CASCADE deletes children."""
    with session_storage._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row["name"] for row in cursor.fetchall()}
        assert "rag_sessions" in tables
        assert "rag_messages" in tables
        assert "rag_traces" in tables

        cursor.execute("PRAGMA journal_mode;")
        journal = cursor.fetchone()[0]
        assert journal.lower() == "wal"

        cursor.execute("PRAGMA foreign_keys;")
        fk = cursor.fetchone()[0]
        assert fk == 1

    # Test Cascade deletion
    session = session_storage.create_session(title="Test Cascade")
    session_storage.append_message(session.session_id, role="user", content="Hello world")
    session_storage.append_trace(session.session_id, trace_text="Step 1 complete")

    assert len(session_storage.get_messages(session.session_id)) == 1
    assert len(session_storage.get_traces(session.session_id)) == 1

    # Delete session
    deleted = session_storage.delete_session(session.session_id)
    assert deleted is True

    assert session_storage.get_session(session.session_id) is None
    assert len(session_storage.get_messages(session.session_id)) == 0
    assert len(session_storage.get_traces(session.session_id)) == 0


def test_session_storage_crud_and_sorting(session_storage: SessionStorage):
    """Verify session creation, default titles, sorting by updated_at, and updates."""
    # Create session with intention
    s1 = session_storage.create_session(
        intention="Build an async CLI pipeline",
        active_gems=["thor", "async"],
        metadata={"priority": "high"},
    )
    assert s1.title == "Build an async CLI pipeline"
    assert s1.active_gems == ["thor", "async"]
    assert s1.metadata == {"priority": "high"}

    # Create session with gems only (no intention)
    s2 = session_storage.create_session(
        active_gems=["sinatra", "puma"],
    )
    assert "sinatra, puma" in s2.title

    # List sessions (s2 updated after s1)
    sessions = session_storage.list_sessions()
    assert len(sessions) == 2
    assert sessions[0].session_id == s2.session_id

    # Latest session
    latest = session_storage.get_latest_session()
    assert latest is not None
    assert latest.session_id == s2.session_id

    # Update s1
    updated_s1 = session_storage.update_session(
        s1.session_id,
        title="Updated Title",
        intention="New intention",
        status="completed",
    )
    assert updated_s1 is not None
    assert updated_s1.title == "Updated Title"
    assert updated_s1.status == "completed"

    # Now s1 should be latest because it was just updated
    latest_after_update = session_storage.get_latest_session()
    assert latest_after_update is not None
    assert latest_after_update.session_id == s1.session_id


def test_session_storage_messages_and_traces(session_storage: SessionStorage):
    """Verify message turns, node citation persistence, and trace limits."""
    s = session_storage.create_session()

    # Append user turn
    msg1 = session_storage.append_message(
        s.session_id,
        role="user",
        content="What is Thor?",
    )
    assert msg1.role == "user"
    assert msg1.content == "What is Thor?"

    # Append agent turn with retrieved nodes
    msg2 = session_storage.append_message(
        s.session_id,
        role="agent",
        content="Thor is a CLI library.",
        retrieved_nodes=["method:Thor#start", "class:Thor"],
    )
    assert msg2.role == "agent"
    assert msg2.retrieved_nodes == ["method:Thor#start", "class:Thor"]

    messages = session_storage.get_messages(s.session_id)
    assert len(messages) == 2
    assert messages[0].content == "What is Thor?"
    assert messages[1].retrieved_nodes == ["method:Thor#start", "class:Thor"]

    # Append traces
    for i in range(10):
        session_storage.append_trace(s.session_id, f"Trace step {i}")

    all_traces = session_storage.get_traces(s.session_id)
    assert len(all_traces) == 10

    limited_traces = session_storage.get_traces(s.session_id, limit=3)
    assert len(limited_traces) == 3
    assert limited_traces[-1].trace_text == "Trace step 9"


def test_session_storage_export_transcript(session_storage: SessionStorage):
    """Verify formatted markdown and JSON transcript generation."""
    s = session_storage.create_session(
        title="CLI Pipeline Design",
        active_gems=["thor", "gli"],
        intention="Design command parsing",
    )
    session_storage.append_message(s.session_id, role="user", content="How do I structure CLI commands?")
    session_storage.append_message(
        s.session_id,
        role="agent",
        content="Use `Thor::Group`.",
        retrieved_nodes=["class:Thor::Group"],
    )
    session_storage.append_trace(s.session_id, "Extracted 42 AST nodes from thor")

    # Export markdown
    md_text, json_dict = session_storage.export_transcript(s.session_id, format="markdown")
    assert "# RubyGemDB Contextual GraphRAG Transcript: CLI Pipeline Design" in md_text
    assert f"**Session ID:** `{s.session_id}`" in md_text
    assert "Design command parsing" in md_text
    assert "thor, gli" in md_text
    assert "How do I structure CLI commands?" in md_text
    assert "Use `Thor::Group`." in md_text
    assert "class:Thor::Group" in md_text
    assert "Extracted 42 AST nodes from thor" in md_text

    # Verify JSON structure
    assert json_dict["session_id"] == s.session_id
    assert json_dict["title"] == "CLI Pipeline Design"
    assert len(json_dict["messages"]) == 2
    assert len(json_dict["traces"]) == 1
    assert json_dict["messages"][1]["retrieved_nodes"] == ["class:Thor::Group"]


# --- 2. TxtaiAgent Integration Tests ---


def test_agent_session_binding(mock_agent_with_storage: TxtaiAgent, session_storage: SessionStorage):
    """Verify TxtaiAgent binds sessions and reloads message history."""
    agent = mock_agent_with_storage

    # Create pre-existing session in storage
    session = session_storage.create_session(
        title="Prior Discussion",
        active_gems=["puma", "falcon"],
        intention="Compare web servers",
    )
    session_storage.append_message(session.session_id, role="user", content="Which server handles async better?")
    session_storage.append_message(session.session_id, role="agent", content="Falcon uses the async gem natively.")

    # Bind agent to the session
    bound_session = agent.bind_session(session.session_id)
    assert bound_session.session_id == session.session_id
    assert agent.active_session_id == session.session_id
    assert agent._graphrag_gems == ["puma", "falcon"]
    assert agent._graphrag_intention == "Compare web servers"

    # Chat history reloaded
    assert len(agent._chat_history) == 2
    assert agent._chat_history[0]["role"] == "user"
    assert agent._chat_history[0]["content"] == "Which server handles async better?"
    assert agent._chat_history[1]["role"] == "agent"


def test_agent_chat_persists_turns_and_indexed_chunks(
    mock_agent_with_storage: TxtaiAgent, session_storage: SessionStorage
):
    """Verify chat() auto-binds, persists user and agent messages, and indexes chunks."""
    agent = mock_agent_with_storage
    mock_service = MagicMock()
    mock_service.nodes_by_id = {"node1": {}}
    mock_service.retrieve.return_value = {
        "primary_nodes": [{"id": "class:Thor"}],
        "expanded_nodes": [{"id": "method:Thor#start"}],
        "edges": [],
    }
    mock_service.format_grounded_context.return_value = "### Grounded API\n- Thor#start"
    agent.graphrag_service = mock_service
    agent._graphrag_gems = ["thor"]
    agent._graphrag_intention = "CLI pipeline"

    fake_model = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = "Call Thor.start to launch."
    fake_model.return_value = fake_resp
    agent.model = fake_model

    # Mock index_memory to spy on chunk indexing calls
    indexed_memories = []
    original_index_memory = agent.index_memory

    def spy_index_memory(text, doc_type, metadata=None):
        indexed_memories.append({"text": text, "doc_type": doc_type, "metadata": metadata})
        original_index_memory(text, doc_type, metadata)

    with patch.object(agent, "index_memory", side_effect=spy_index_memory):
        ans = agent.chat("How do I invoke the CLI?")
        assert ans == "Call Thor.start to launch."

    # Active session was created
    assert agent.active_session_id is not None
    messages = session_storage.get_messages(agent.active_session_id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "How do I invoke the CLI?"
    assert messages[1].role == "agent"
    assert messages[1].content == "Call Thor.start to launch."
    assert "class:Thor" in messages[1].retrieved_nodes
    assert "method:Thor#start" in messages[1].retrieved_nodes

    # Verify both turns were indexed with session_turn doc_type and session_id
    assert len(indexed_memories) == 2
    assert indexed_memories[0]["doc_type"] == "session_turn"
    assert indexed_memories[0]["metadata"]["session_id"] == agent.active_session_id
    assert indexed_memories[0]["metadata"]["role"] == "user"

    assert indexed_memories[1]["doc_type"] == "session_turn"
    assert indexed_memories[1]["metadata"]["session_id"] == agent.active_session_id
    assert indexed_memories[1]["metadata"]["role"] == "agent"


def test_agent_set_graphrag_context_traces(
    mock_agent_with_storage: TxtaiAgent, session_storage: SessionStorage
):
    """Verify set_graphrag_context persists active gems, intention, and progress traces."""
    agent = mock_agent_with_storage
    session = agent.bind_session()

    mock_service = MagicMock()
    mock_service.nodes_by_id = {"n1": {}, "n2": {}}

    def simulate_build(gems, progress_callback=None):
        if progress_callback:
            progress_callback("Cloning repository...")
            progress_callback("Extracting AST...")
        return {"edges": [{}]}

    mock_service.build_or_load_index.side_effect = simulate_build
    agent.graphrag_service = mock_service

    result = agent.set_graphrag_context(["thor"], intention="Custom CLI")
    assert result["gems"] == ["thor"]
    assert result["intention"] == "Custom CLI"

    # Verify session updated
    refreshed = session_storage.get_session(session.session_id)
    assert refreshed is not None
    assert refreshed.active_gems == ["thor"]
    assert refreshed.intention == "Custom CLI"

    # Verify traces captured
    traces = session_storage.get_traces(session.session_id)
    assert len(traces) == 2
    assert "Cloning repository..." in traces[0].trace_text
    assert "Extracting AST..." in traces[1].trace_text


# --- 3. MCP Server Tool Tests ---


@pytest.mark.asyncio
async def test_mcp_session_tools_and_lifecycle(session_storage: SessionStorage):
    """Verify FastMCP tools for listing, getting, resuming, exporting, and deleting sessions."""
    with patch("rubygemdb.mcp_server._get_session_storage", return_value=session_storage):
        # 1. Create sessions in storage
        s1 = session_storage.create_session(
            session_id="session-1",
            title="Session 1",
            active_gems=["thor"],
            intention="CLI design",
        )
        session_storage.append_message(s1.session_id, role="user", content="Hello MCP")
        session_storage.append_message(s1.session_id, role="agent", content="Hello user")
        session_storage.append_trace(s1.session_id, "Container trace 1")

        # 2. list_rag_sessions
        res: Any = await mcp.call_tool("list_rag_sessions", {"limit": 10})
        sessions_list = res[1]["result"]
        assert len(sessions_list) >= 1
        assert any(s["session_id"] == "session-1" for s in sessions_list)

        # 3. get_rag_session
        res = await mcp.call_tool("get_rag_session", {"session_id": "session-1"})
        data = res[1]["result"]
        assert data["session"]["session_id"] == "session-1"
        assert len(data["messages"]) == 2
        assert len(data["traces"]) == 1

        # 4. resume_rag_session
        mock_graphrag = MagicMock()
        mock_graphrag.build_or_load_index.return_value = {"edges": []}
        with patch("rubygemdb.mcp_server._get_graphrag_service", return_value=mock_graphrag):
            res = await mcp.call_tool("resume_rag_session", {"session_id": "session-1"})
            resume_data = res[1]["result"]
            assert resume_data["session_id"] == "session-1"
            assert resume_data["active_gems"] == ["thor"]
            assert resume_data["message_count"] == 2
            mock_graphrag.build_or_load_index.assert_called_once_with(["thor"])

        # 5. export_rag_session_transcript (markdown)
        res = await mcp.call_tool("export_rag_session_transcript", {"session_id": "session-1", "format": "markdown"})
        export_data = res[1]["result"]
        assert export_data["session_id"] == "session-1"
        assert "# RubyGemDB Contextual GraphRAG Transcript: Session 1" in export_data["transcript"]

        # 6. delete_rag_session
        res = await mcp.call_tool("delete_rag_session", {"session_id": "session-1"})
        del_data = res[1]["result"]
        assert del_data["deleted"] is True
        assert session_storage.get_session("session-1") is None


@pytest.mark.asyncio
async def test_mcp_build_and_query_graphrag_with_session_id(session_storage: SessionStorage):
    """Verify build_gem_graphrag and query_gem_graphrag accept and record session_id."""
    mock_service = MagicMock()
    mock_service.active_gems = ["thor"]
    mock_service.active_hash = "12345"
    mock_service.nodes_by_id = {"node:1": {}}
    mock_service.build_or_load_index.return_value = {"edges": []}
    mock_service.retrieve.return_value = {
        "primary_nodes": [{"id": "class:Thor"}],
        "expanded_nodes": [],
        "edges": [],
    }
    mock_service.format_grounded_context.return_value = "### Thor Grounded Context"

    with (
        patch("rubygemdb.mcp_server._get_session_storage", return_value=session_storage),
        patch("rubygemdb.mcp_server._get_graphrag_service", return_value=mock_service),
    ):
        # build_gem_graphrag with new session
        res_build: Any = await mcp.call_tool(
            "build_gem_graphrag",
            {"gems": ["thor"], "intention": "CLI tool", "session_id": "mcp-session-xyz"},
        )
        build_result = res_build[1]["result"]
        assert build_result["session_id"] == "mcp-session-xyz"
        assert build_result["status"] == "ready"

        session = session_storage.get_session("mcp-session-xyz")
        assert session is not None
        assert session.active_gems == ["thor"]
        assert session.intention == "CLI tool"

        # query_gem_graphrag with existing session
        res_query: Any = await mcp.call_tool(
            "query_gem_graphrag",
            {"gems": ["thor"], "query": "How to define commands?", "session_id": "mcp-session-xyz"},
        )
        query_result = res_query[1]["result"]
        assert query_result["session_id"] == "mcp-session-xyz"
        assert "### Thor Grounded Context" in query_result["grounded_context"]

        # Verify messages persisted in session
        messages = session_storage.get_messages("mcp-session-xyz")
        assert len(messages) == 2
        assert messages[0].content == "How to define commands?"
        assert messages[1].content == "### Thor Grounded Context"
        assert messages[1].retrieved_nodes == ["class:Thor"]


# --- 4. Textual TUI AgentChatTab Tests ---


class _TestChatApp(App):
    """Test host application embedding AgentChatTab."""

    def __init__(self, tab: AgentChatTab):
        super().__init__()
        self.tab = tab

    def compose(self) -> ComposeResult:
        yield self.tab


@pytest.mark.asyncio
async def test_tui_chat_tab_auto_restores_and_switches_sessions(session_storage: SessionStorage):
    """Verify AgentChatTab mounts, restores latest session, and switches between sessions."""
    # Seed 2 sessions
    s1 = session_storage.create_session(
        session_id="session-alpha",
        title="Session Alpha",
        active_gems=["thor"],
        intention="Build CLI Alpha",
    )
    session_storage.append_message(s1.session_id, role="user", content="Alpha query")
    session_storage.append_message(s1.session_id, role="agent", content="Alpha answer")
    session_storage.append_trace(s1.session_id, "Alpha trace")

    s2 = session_storage.create_session(
        session_id="session-beta",
        title="Session Beta",
        active_gems=["sinatra"],
        intention="Build Web Beta",
    )
    session_storage.append_message(s2.session_id, role="user", content="Beta query")
    session_storage.append_message(s2.session_id, role="agent", content="Beta answer")
    session_storage.append_trace(s2.session_id, "Beta trace")

    mock_agent = MagicMock()
    tab = AgentChatTab(lambda: mock_agent, session_storage=session_storage)
    app = _TestChatApp(tab)

    async with app.run_test() as pilot:
        await pilot.pause()

        # Latest session (s2) should be loaded on mount
        assert tab.current_session_id == "session-beta"
        intention_input = tab.query_one("#intention-input", Input)
        assert intention_input.value == "Build Web Beta"
        assert tab.selected_gems == ["sinatra"]

        # Chat log should contain s2 messages
        assert tab.query_one("#chat-log", RichLog) is not None
        assert len(tab._chat_messages) == 2
        assert tab._chat_messages[0]["content"] == "Beta query"

        # Switch to session-alpha
        tab.load_session("session-alpha")
        await pilot.pause()

        assert tab.current_session_id == "session-alpha"
        assert intention_input.value == "Build CLI Alpha"
        assert tab.selected_gems == ["thor"]
        assert len(tab._chat_messages) == 2
        assert tab._chat_messages[0]["content"] == "Alpha query"

        # Test New Session button
        new_btn = tab.query_one("#new-session-btn", Button)
        new_btn.press()
        await pilot.pause()

        assert tab.current_session_id != "session-alpha"
        assert tab.current_session_id != "session-beta"
        assert intention_input.value == ""
        assert tab.selected_gems == []

        # Test Delete Session button
        fresh_id = tab.current_session_id
        del_btn = tab.query_one("#delete-session-btn", Button)
        del_btn.press()
        await pilot.pause()

        assert session_storage.get_session(fresh_id) is None
        assert tab.current_session_id in ("session-alpha", "session-beta")
