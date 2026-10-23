"""Pydantic data models for GraphRAG session tracking and conversational history."""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class RAGSession(BaseModel):
    """Represents a GraphRAG conversational session."""

    session_id: str
    title: str = ""
    created_at: str
    updated_at: str
    active_gems: list[str] = Field(default_factory=list)
    intention: str = ""
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGCitation(BaseModel):
    """Citation metadata for an AST symbol or code snippet in GraphRAG context."""

    id: str
    gem: str
    node_id: str
    node_type: str
    symbol: str
    file: Optional[str] = None
    signature: Optional[str] = None
    summary: Optional[str] = None


class RAGMessage(BaseModel):
    """Represents a single message turn in a GraphRAG session."""

    id: Optional[int] = None
    session_id: str
    role: Literal["user", "agent", "system", "error"]
    content: str
    timestamp: str
    retrieved_nodes: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    trace_step: Optional[str] = None


class RAGTrace(BaseModel):
    """Represents an execution or container step trace in a GraphRAG session."""

    id: Optional[int] = None
    session_id: str
    trace_text: str
    timestamp: str


class SessionTranscript(BaseModel):
    """Exportable transcript representation of a GraphRAG session."""

    session_id: str
    title: str = ""
    created_at: str
    updated_at: str
    active_gems: list[str] = Field(default_factory=list)
    intention: str = ""
    status: str = "active"
    messages: list[RAGMessage] = Field(default_factory=list)
    traces: list[RAGTrace] = Field(default_factory=list)
    markdown: str = ""
    json_data: dict[str, Any] = Field(default_factory=dict)
