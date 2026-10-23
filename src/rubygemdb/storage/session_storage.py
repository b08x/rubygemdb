"""Dedicated SQLite session storage for GraphRAG sessions, message history, and execution traces."""

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Optional, Union
import uuid

from rubygemdb.core.config import settings
from rubygemdb.models.session import RAGMessage, RAGSession, RAGTrace


class SessionStorage:
    """Thread-safe SQLite storage for GraphRAG session tracking and conversational history."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        self.db_path = Path(db_path) if db_path else Path(settings.session_db_file)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a connection with WAL mode and foreign key enforcement."""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_db(self) -> None:
        """Initialize database tables and indexes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rag_sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    active_gems TEXT NOT NULL DEFAULT '[]',
                    intention TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rag_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    retrieved_nodes TEXT NOT NULL DEFAULT '[]',
                    trace_step TEXT,
                    FOREIGN KEY (session_id) REFERENCES rag_sessions(session_id) ON DELETE CASCADE
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rag_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    trace_text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES rag_sessions(session_id) ON DELETE CASCADE
                );
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_messages_session ON rag_messages(session_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_traces_session ON rag_traces(session_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_sessions_updated ON rag_sessions(updated_at DESC);")
            conn.commit()

    def _row_to_session(self, row: sqlite3.Row) -> RAGSession:
        active_gems = json.loads(row["active_gems"]) if row["active_gems"] else []
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        return RAGSession(
            session_id=row["session_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            active_gems=active_gems,
            intention=row["intention"],
            status=row["status"],
            metadata=metadata,
        )

    def _row_to_message(self, row: sqlite3.Row) -> RAGMessage:
        retrieved = json.loads(row["retrieved_nodes"]) if row["retrieved_nodes"] else []
        return RAGMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            timestamp=row["timestamp"],
            retrieved_nodes=retrieved,
            trace_step=row["trace_step"],
        )

    def _row_to_trace(self, row: sqlite3.Row) -> RAGTrace:
        return RAGTrace(
            id=row["id"],
            session_id=row["session_id"],
            trace_text=row["trace_text"],
            timestamp=row["timestamp"],
        )

    def create_session(
        self,
        session_id: Optional[str] = None,
        title: str = "",
        active_gems: Optional[list[str]] = None,
        intention: str = "",
        metadata: Optional[dict[str, Any]] = None,
        status: str = "active",
    ) -> RAGSession:
        """Create and persist a new GraphRAG session."""
        sid = session_id or str(uuid.uuid4())
        now = datetime.now().isoformat()
        gems = active_gems or []
        meta = metadata or {}

        if not title:
            if intention:
                title = intention[:40]
            elif gems:
                title = f"Gems: {', '.join(gems[:3])}"
            else:
                title = f"Session {sid[:8]}"

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO rag_sessions (session_id, title, created_at, updated_at, active_gems, intention, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    title,
                    now,
                    now,
                    json.dumps(gems),
                    intention,
                    status,
                    json.dumps(meta),
                ),
            )
            conn.commit()

        return RAGSession(
            session_id=sid,
            title=title,
            created_at=now,
            updated_at=now,
            active_gems=gems,
            intention=intention,
            status=status,
            metadata=meta,
        )

    def get_session(self, session_id: str) -> Optional[RAGSession]:
        """Fetch a session by its unique ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM rag_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_session(row)
        return None

    def list_sessions(self, limit: int = 50) -> list[RAGSession]:
        """List sessions ordered by most recently updated."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM rag_sessions ORDER BY updated_at DESC LIMIT ?",
                (max(1, limit),),
            )
            return [self._row_to_session(row) for row in cursor.fetchall()]

    def get_latest_session(self) -> Optional[RAGSession]:
        """Get the most recently updated session, or None."""
        sessions = self.list_sessions(limit=1)
        return sessions[0] if sessions else None

    def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        active_gems: Optional[list[str]] = None,
        intention: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[RAGSession]:
        """Update mutable fields of a session."""
        existing = self.get_session(session_id)
        if not existing:
            return None

        new_title = title if title is not None else existing.title
        new_gems = active_gems if active_gems is not None else existing.active_gems
        new_intention = intention if intention is not None else existing.intention
        new_status = status if status is not None else existing.status
        new_metadata = metadata if metadata is not None else existing.metadata
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE rag_sessions
                SET title = ?, active_gems = ?, intention = ?, status = ?, metadata = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    new_title,
                    json.dumps(new_gems),
                    new_intention,
                    new_status,
                    json.dumps(new_metadata),
                    now,
                    session_id,
                ),
            )
            conn.commit()

        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its associated messages and traces."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM rag_sessions WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        retrieved_nodes: Optional[list[str]] = None,
        trace_step: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> RAGMessage:
        """Append a conversational message to a session and touch updated_at."""
        if not self.get_session(session_id):
            self.create_session(session_id=session_id)

        now = timestamp or datetime.now().isoformat()
        nodes = retrieved_nodes or []

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO rag_messages (session_id, role, content, timestamp, retrieved_nodes, trace_step)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    content,
                    now,
                    json.dumps(nodes),
                    trace_step,
                ),
            )
            msg_id = cursor.lastrowid
            conn.execute(
                "UPDATE rag_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            conn.commit()

        return RAGMessage(
            id=msg_id,
            session_id=session_id,
            role=role,  # type: ignore[arg-type]
            content=content,
            timestamp=now,
            retrieved_nodes=nodes,
            trace_step=trace_step,
        )

    def get_messages(self, session_id: str) -> list[RAGMessage]:
        """Fetch all messages for a session in chronological order."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM rag_messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
            return [self._row_to_message(row) for row in cursor.fetchall()]

    def append_trace(
        self,
        session_id: str,
        trace_text: str,
        timestamp: Optional[str] = None,
    ) -> RAGTrace:
        """Append an execution/container trace to a session and touch updated_at."""
        if not self.get_session(session_id):
            self.create_session(session_id=session_id)

        now = timestamp or datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO rag_traces (session_id, trace_text, timestamp)
                VALUES (?, ?, ?)
                """,
                (session_id, trace_text, now),
            )
            trace_id = cursor.lastrowid
            conn.execute(
                "UPDATE rag_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            conn.commit()

        return RAGTrace(
            id=trace_id,
            session_id=session_id,
            trace_text=trace_text,
            timestamp=now,
        )

    def get_traces(self, session_id: str, limit: Optional[int] = None) -> list[RAGTrace]:
        """Fetch execution traces for a session in chronological order."""
        with self._get_connection() as conn:
            if limit and limit > 0:
                cursor = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM rag_traces WHERE session_id = ? ORDER BY id DESC LIMIT ?
                    ) ORDER BY id ASC
                    """,
                    (session_id, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM rag_traces WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                )
            return [self._row_to_trace(row) for row in cursor.fetchall()]

    def export_transcript(
        self,
        session_id: str,
        format: str = "markdown",
    ) -> tuple[str, dict[str, Any]]:
        """Generate markdown and JSON transcript for a session."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session '{session_id}' not found.")

        messages = self.get_messages(session_id)
        traces = self.get_traces(session_id)

        # Build Markdown
        md_lines = [
            f"# RubyGemDB Contextual GraphRAG Transcript: {session.title}",
            "",
            f"**Session ID:** `{session.session_id}`",
            f"**Status:** {session.status}",
            f"**Created:** {session.created_at}",
            f"**Last Updated:** {session.updated_at}",
            f"**Project Intention:** {session.intention or 'None specified'}",
            f"**Indexed Gems:** {', '.join(session.active_gems) or 'None'}",
            "",
            "---",
            "",
            "## Conversation",
            "",
        ]

        if not messages:
            md_lines.append("*(No conversation messages recorded)*\n")
        else:
            for msg in messages:
                role_label = msg.role.capitalize()
                md_lines.append(f"### {role_label} ({msg.timestamp})")
                md_lines.append(msg.content)
                if msg.retrieved_nodes:
                    md_lines.append("")
                    md_lines.append(f"> **Retrieved AST Nodes:** {', '.join(f'`{n}`' for n in msg.retrieved_nodes)}")
                md_lines.append("")

        if traces:
            md_lines.extend([
                "---",
                "",
                "## Execution Traces",
                "",
            ])
            for t in traces:
                md_lines.append(f"- `[{t.timestamp}]` {t.trace_text}")
            md_lines.append("")

        md_content = "\n".join(md_lines)

        # Build JSON data
        json_dict: dict[str, Any] = {
            "session_id": session.session_id,
            "title": session.title,
            "status": session.status,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "active_gems": session.active_gems,
            "intention": session.intention,
            "metadata": session.metadata,
            "messages": [m.model_dump() for m in messages],
            "traces": [t.model_dump() for t in traces],
        }

        output_str = md_content if format.lower() == "markdown" else json.dumps(json_dict, indent=2)
        return output_str, json_dict
