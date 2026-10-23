"""Model Context Protocol (MCP) server for RubyGemDB.

Enables AI coding agents (Claude Code, Antigravity, OpenCode, Cursor) to discover
Ruby gems, query classifications and architectural layers, and compose layered
gem stacks.

Exposes an executable `rubygemdb-mcp` CLI entrypoint running over stdio by default
for host agent child-process spawning, alongside `--daemon start|status|stop` lifecycle
commands to run detached in the background over SSE.
"""

import argparse
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from mcp.server.fastmcp import FastMCP

from rubygemdb.core.config import settings
from rubygemdb.models.categories import CATEGORIES, CATEGORY_BY_SLUG, LAYERS
from rubygemdb.services.hybrid_search import SearchCallable, hybrid_search
from rubygemdb.services.stack import StackService, guess_category, seed_gems
from rubygemdb.storage.sqlite_storage import SQLiteStorage
from rubygemdb.services.tracing import trace_span

logger = logging.getLogger("rubygemdb.mcp")

mcp = FastMCP(
    name="rubygemdb",
    instructions=(
        "Ruby gem analysis and architectural classification server. "
        "Provides tools to search gems across 21 architectural categories, "
        "inspect gem capabilities, dependencies, and risks, and compose "
        "layered gem stacks (substrate -> plumbing -> composition -> quality)."
    ),
)

_lazy_vector_search: Optional[SearchCallable] = None
_lazy_vector_search_loaded: bool = False


def _get_read_only_storage(db_path: Optional[str] = None) -> SQLiteStorage:
    """Instantiate a strictly read-only SQLiteStorage instance."""
    resolved_path = db_path or str(settings.sqlite_db_file)
    return SQLiteStorage(read_only=True, db_path=resolved_path)


def _get_lazy_vector_search() -> Optional[SearchCallable]:
    """Load txtai embeddings lazily on demand if available, without blocking startup."""
    global _lazy_vector_search, _lazy_vector_search_loaded
    if not _lazy_vector_search_loaded:
        _lazy_vector_search_loaded = True
        index_path = settings.txtai_dir / "rubygems_index"
        if index_path.exists():
            try:
                from txtai import Embeddings  # type: ignore

                embeddings = Embeddings(
                    path=settings.embedding_model_path,
                    backend="sqlite",
                    content=True,
                    gpu=settings.txtai_gpu,
                )
                embeddings.load(str(index_path))

                def _vs(query: str, categories: Optional[List[str]], limit: int) -> List[Dict[str, Any]]:
                    results = embeddings.search(query, limit)
                    matches: List[Dict[str, Any]] = []
                    for r in results:
                        if isinstance(r, (tuple, list)) and len(r) == 2:
                            r = {"id": r[0], "score": r[1]}
                        if not isinstance(r, dict):
                            continue
                        name = None
                        text = str(r.get("text") or "")
                        m = re.match(r"^Gem Name: (\S+)", text)
                        if m:
                            name = m.group(1)
                        elif r.get("type") in (None, "gem") and r.get("name"):
                            name = str(r["name"])
                        if not name:
                            continue
                        cat = r.get("category") or guess_category(name)
                        if categories and cat not in categories:
                            continue
                        matches.append({
                            "name": name,
                            "category": cat,
                            "description": text,
                            "score": float(r.get("score", 0.0)),
                        })
                    return matches

                _lazy_vector_search = _vs
            except Exception as e:
                logger.warning("Could not load txtai embeddings for vector search: %s", e)
                _lazy_vector_search = None
    return _lazy_vector_search


# --- MCP Tools ---


@mcp.tool()
def search_gems(
    query: str,
    category: Optional[str] = None,
    phase: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Search Ruby gems using SQLite FTS5 keyword matching and architectural metadata.

    Supports filtering by architectural category or build phase. Deferring heavy
    embeddings allows sub-millisecond responses for almost all queries.

    Args:
        query: Free-text search query (e.g. 'html parser', 'terminal ui', 'jwt auth').
        category: Optional category slug (e.g. 'document_parsing', 'cli_libraries', 'ai_llm').
        phase: Optional build phase filter ('skeleton', 'input', 'processing', 'output').
        limit: Maximum number of results to return (default: 10).
    """
    db_uri = f"file:{Path(settings.sqlite_db_file).resolve()}?mode=ro"

    # Fast sub-millisecond keyword FTS5 match first
    results = hybrid_search(
        query=query,
        phase=phase,
        category=category,
        limit=limit,
        vector_search=None,
        db_path=db_uri,
    )

    # If no FTS hits, attempt lazy vector fallback if index is available
    if not results:
        vs = _get_lazy_vector_search()
        if vs is not None:
            results = hybrid_search(
                query=query,
                phase=phase,
                category=category,
                limit=limit,
                vector_search=vs,
                db_path=db_uri,
            )

    return results


@mcp.tool()
def get_gem(name: str) -> Dict[str, Any]:
    """Retrieve complete architectural metadata, layer, sub-categories, agent description,
    dependencies, and risks for a named Ruby gem.

    Args:
        name: The exact name of the gem (e.g. 'nokogiri', 'thor', 'dotenv').
    """
    storage = _get_read_only_storage()
    gem = storage.get_classified_gem(name)
    if gem:
        cat = CATEGORY_BY_SLUG.get(gem.classification.primary)
        return {
            "name": gem.name,
            "category": gem.classification.primary,
            "layer": cat.layer if cat else "unknown",
            "sub_categories": gem.classification.sub_categories,
            "confidence": gem.classification.confidence,
            "description": gem.description,
            "agent_description": gem.role.get("description", gem.description or ""),
            "role": gem.role,
            "capabilities": gem.capabilities,
            "dependencies": gem.dependencies,
            "risks": gem.risks.model_dump(),
            "signals": gem.signals.model_dump(),
            "homepage": gem.homepage,
            "source_code_uri": gem.source_code_uri,
            "context7_id": gem.context7_id,
            "preferred": gem.preferred,
            "classified": True,
        }

    # Check unclassified inventory fallback
    inv = storage.get_inventory_gem(name)
    if inv:
        cat_name = inv.get("category") or guess_category(name)
        cat = CATEGORY_BY_SLUG.get(cat_name)
        return {
            "name": name,
            "category": cat_name,
            "layer": cat.layer if cat else "unknown",
            "sub_categories": [],
            "confidence": 0.0,
            "description": inv.get("description"),
            "agent_description": inv.get("description") or "",
            "role": {},
            "capabilities": [],
            "dependencies": [],
            "risks": {"invasiveness": 1, "coupling": 1, "abstraction_leak": "low"},
            "signals": {"rails": False, "external_io": False, "native_ext": False},
            "homepage": inv.get("homepage"),
            "source_code_uri": inv.get("source_code_uri"),
            "context7_id": inv.get("context7_id"),
            "preferred": bool(inv.get("preferred")),
            "classified": False,
        }

    return {"error": f"Gem '{name}' not found in database."}


@mcp.tool()
def compose_stack(query: str) -> Dict[str, Any]:
    """Compose a layered Ruby gem stack manifest and Gemfile snippet for a context query.

    Organizes recommendations across architectural layers (substrate -> plumbing -> composition -> quality)
    starting from curated base picks. Operates strictly read-only.

    Args:
        query: Description of the application or tooling being built (e.g. 'CLI data pipeline tool').
    """
    storage = _get_read_only_storage()
    service = StackService(storage=storage, read_only=True)
    manifest = service.build_stack(query)
    gemfile = service.render_gemfile(manifest)
    manifest_yaml = service.render_manifest_yaml(manifest)

    return {
        "context": manifest.get("context", query),
        "layers": manifest.get("layers", []),
        "gemfile": gemfile,
        "manifest_yaml": manifest_yaml,
    }


@mcp.tool()
def list_categories() -> List[Dict[str, Any]]:
    """List the 21 architectural categories, stack layers, descriptions, and curated base gems."""
    return [
        {
            "slug": c.slug,
            "label": c.label,
            "layer": c.layer,
            "description": c.description,
            "base_gems": c.base_gems,
            "sub_categories": list(c.sub_categories.keys()),
            "sub_categories_with_examples": c.sub_categories,
        }
        for c in CATEGORIES
    ]


_graphrag_service: Optional[Any] = None


_session_storage: Any = None


def _get_session_storage(db_path: Optional[str] = None) -> Any:
    """Lazily load SessionStorage."""
    global _session_storage
    if _session_storage is None or db_path is not None:
        from rubygemdb.storage.session_storage import SessionStorage

        _session_storage = SessionStorage(db_path=db_path)
    return _session_storage


def _get_graphrag_service() -> Any:
    """Lazily load GraphRAGService to avoid heavy initial imports."""
    global _graphrag_service
    if _graphrag_service is None:
        from rubygemdb.services.graphrag import GraphRAGService

        _graphrag_service = GraphRAGService()
    return _graphrag_service


@mcp.tool()
def list_rag_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    """List tracked GraphRAG sessions, ordered by most recently updated.

    Args:
        limit: Maximum number of sessions to return (default 20).

    Returns:
        List of session metadata dictionaries.
    """
    storage = _get_session_storage()
    sessions = storage.list_sessions(limit=limit)
    return [s.model_dump() for s in sessions]


@mcp.tool()
def get_rag_session(session_id: str) -> Dict[str, Any]:
    """Retrieve full details of a GraphRAG session including message history and traces.

    Args:
        session_id: Unique session UUID.

    Returns:
        Dictionary containing session metadata, messages, and execution traces.
    """
    storage = _get_session_storage()
    session = storage.get_session(session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found.")
    messages = storage.get_messages(session_id)
    traces = storage.get_traces(session_id)
    return {
        "session": session.model_dump(),
        "messages": [m.model_dump() for m in messages],
        "traces": [t.model_dump() for t in traces],
    }


@mcp.tool()
def resume_rag_session(session_id: str) -> Dict[str, Any]:
    """Resume an existing GraphRAG session, pre-warming its active gems if present.

    Args:
        session_id: Unique session UUID to resume.

    Returns:
        Summary dictionary with session status, active gems, and recent conversation turns.
    """
    storage = _get_session_storage()
    session = storage.get_session(session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found.")
    if session.active_gems:
        service = _get_graphrag_service()
        service.build_or_load_index(session.active_gems)
    messages = storage.get_messages(session_id)
    return {
        "session_id": session.session_id,
        "title": session.title,
        "active_gems": session.active_gems,
        "intention": session.intention,
        "status": session.status,
        "message_count": len(messages),
        "latest_messages": [m.model_dump() for m in messages[-5:]],
    }


@mcp.tool()
def export_rag_session_transcript(
    session_id: str, format: str = "markdown"
) -> Dict[str, Any]:
    """Export a formatted transcript (markdown or JSON) of a tracked GraphRAG session.

    Args:
        session_id: Unique session UUID.
        format: Export format, either 'markdown' or 'json' (default 'markdown').

    Returns:
        Dictionary containing the formatted transcript string and structured data.
    """
    storage = _get_session_storage()
    transcript_text, json_dict = storage.export_transcript(session_id, format=format)
    return {
        "session_id": session_id,
        "format": format,
        "transcript": transcript_text,
        "data": json_dict,
    }


@mcp.tool()
def delete_rag_session(session_id: str) -> Dict[str, Any]:
    """Delete a GraphRAG session, its message history, and execution traces.

    Args:
        session_id: Unique session UUID to delete.

    Returns:
        Dictionary indicating whether the session was deleted.
    """
    storage = _get_session_storage()
    deleted = storage.delete_session(session_id)
    return {
        "session_id": session_id,
        "deleted": deleted,
    }


@mcp.tool()
def build_gem_graphrag(
    gems: List[str], intention: str = "", session_id: Optional[str] = None
) -> Dict[str, Any]:
    """Pre-warm and index the AST symbol hierarchy and decode documentation for a set of gems.

    Args:
        gems: List of Ruby gem names to clone/unpack and index.
        intention: Optional project intention or architectural context.
        session_id: Optional session UUID to bind and persist context.

    Returns:
        Dictionary with session_id, active gems, hash, node counts, and edge statistics.
    """
    sid = session_id or str(uuid.uuid4())
    with trace_span(
        name="mcp-build-gem-graphrag",
        session_id=sid,
        input={"gems": gems, "intention": intention},
        tags=["rubygemdb", "mcp", "indexing"],
    ) as span:
        storage = _get_session_storage()
        session = storage.get_session(sid)
        if not session:
            storage.create_session(
                session_id=sid,
                active_gems=gems,
                intention=intention,
            )
        else:
            storage.update_session(sid, active_gems=gems, intention=intention)

        service = _get_graphrag_service()
        data = service.build_or_load_index(gems)
        res = {
            "session_id": sid,
            "gems": service.active_gems,
            "hash": service.active_hash,
            "intention": intention,
            "nodes_count": len(service.nodes_by_id),
            "edges_count": len(data.get("edges", [])),
            "status": "ready",
        }
        span.update(output={
            "session_id": sid,
            "nodes_count": res["nodes_count"],
            "edges_count": res["edges_count"],
            "status": "ready",
        })
        return res


@mcp.tool()
def query_gem_graphrag(
    gems: List[str],
    query: str,
    intention: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a 2-stage GraphRAG query over selected gems, returning grounded AST signatures, decode parameter types, and code examples.

    Args:
        gems: List of Ruby gem names to query (will auto-index if not already cached).
        query: Natural language query about gem capabilities, methods, or classes.
        intention: Optional project intention for grounding context.
        session_id: Optional session UUID to track conversation turns.

    Returns:
        Dictionary with session_id, retrieval results (primary nodes, expanded nodes, matched edges, and formatted markdown context).
    """
    sid = session_id or str(uuid.uuid4())
    with trace_span(
        name="mcp-query-gem-graphrag",
        session_id=sid,
        input={"gems": gems, "query": query, "intention": intention},
        tags=["rubygemdb", "mcp", "graphrag"],
    ) as span:
        storage = _get_session_storage()
        session = storage.get_session(sid)
        if not session:
            storage.create_session(
                session_id=sid,
                active_gems=gems,
                intention=intention or "",
            )
        elif gems:
            storage.update_session(sid, active_gems=gems, intention=intention or session.intention)

        service = _get_graphrag_service()
        service.build_or_load_index(gems)
        retrieval = service.retrieve(query, top_k=5)
        grounded_context = service.format_grounded_context(retrieval, intention=intention)
        citations = retrieval.get("citations", [])

        retrieved_node_ids: List[str] = []
        for n in retrieval.get("primary_nodes", []):
            if isinstance(n, dict) and "id" in n:
                retrieved_node_ids.append(str(n["id"]))
            elif isinstance(n, str):
                retrieved_node_ids.append(n)
        for n in retrieval.get("expanded_nodes", []):
            if isinstance(n, dict) and "id" in n:
                retrieved_node_ids.append(str(n["id"]))
            elif isinstance(n, str):
                retrieved_node_ids.append(n)

        storage.append_message(sid, role="user", content=query)
        storage.append_message(
            sid,
            role="agent",
            content=grounded_context,
            retrieved_nodes=retrieved_node_ids,
            citations=citations,
        )

        res = {
            "session_id": sid,
            "gems": service.active_gems,
            "query": query,
            "intention": intention,
            "primary_nodes": retrieval.get("primary_nodes", []),
            "expanded_nodes": retrieval.get("expanded_nodes", []),
            "edges": retrieval.get("edges", []),
            "citations": citations,
            "grounded_context": grounded_context,
        }
        span.update(output={
            "session_id": sid,
            "primary_nodes_count": len(res["primary_nodes"]),
            "expanded_nodes_count": len(res["expanded_nodes"]),
            "edges_count": len(res["edges"]),
            "citations_count": len(citations),
        })
        return res


# --- MCP Resources ---


@mcp.resource("rubygemdb://taxonomy", mime_type="text/markdown")
def get_taxonomy_resource() -> str:
    """Markdown reference of all 21 categories, layer associations, and sub-category vocabularies."""
    lines = [
        "# RubyGemDB Architectural Taxonomy",
        "",
        "RubyGemDB organizes Ruby gems into 21 functional categories across 4 architectural stack layers:",
        "",
    ]
    for layer in LAYERS:
        lines.append(f"## Layer: {layer.capitalize()}")
        lines.append("")
        layer_categories = [c for c in CATEGORIES if c.layer == layer]
        for c in layer_categories:
            lines.append(f"### {c.label} (`{c.slug}`)")
            lines.append(f"- **Description**: {c.description}")
            if c.base_gems:
                lines.append(f"- **Base Seeds**: {', '.join(f'`{g}`' for g in c.base_gems)}")
            if c.sub_categories:
                sub_cats_str = ", ".join(f"`{k}`" for k in c.sub_categories.keys())
                lines.append(f"- **Sub-categories**: {sub_cats_str}")
            lines.append("")
    return "\n".join(lines)


@mcp.resource("rubygemdb://stack-seeds", mime_type="text/markdown")
def get_stack_seeds_resource() -> str:
    """Markdown reference of curated base gems grouped by architectural layer."""
    lines = [
        "# Curated RubyGemDB Stack Seeds",
        "",
        "Curated base gem picks for scaffolding robust Ruby applications and back-end tooling:",
        "",
    ]
    seeds = seed_gems()
    for layer in LAYERS:
        lines.append(f"## {layer.capitalize()} Layer")
        layer_seeds = [s for s in seeds if s["layer"] == layer]
        if not layer_seeds:
            lines.append("- *(No seeds configured for this layer)*")
        else:
            for s in layer_seeds:
                lines.append(f"- `{s['gem']}` (Category: `{s['category']}`)")
        lines.append("")
    return "\n".join(lines)


# --- MCP Prompts ---


@mcp.prompt("design-ruby-stack")
def design_ruby_stack(project_description: str) -> str:
    """Prompt template guiding agents to explore categories, search candidates layer-by-layer, and output a validated stack."""
    return f"""You are an expert Ruby systems architect designing a robust, layered gem stack for:

"{project_description}"

Follow this disciplined step-by-step procedure:

1. **Taxonomy & Layer Orientation**:
   - Inspect the 21 architectural categories using `list_categories` or reading resource `rubygemdb://taxonomy`.
   - Review standard stack seeds with resource `rubygemdb://stack-seeds` (e.g. `dotenv`, `drydock`, `pry`, `rubocop`).

2. **Layer-by-Layer Discovery**:
   - For each stack layer (Substrate -> Plumbing -> Composition -> Quality), run targeted queries using `search_gems(query=..., category=...)`.
   - For candidate gems, call `get_gem(name=...)` to check their dependencies, capabilities, and invasiveness/coupling risks.

3. **Compose & Validate Stack**:
   - Call `compose_stack(query="{project_description}")` to generate a candidate manifest and Gemfile snippet.
   - Refine the selection to ensure clean abstraction boundaries and minimal dependency bloat.
"""


@mcp.prompt("gem-architect-rag")
def gem_architect_rag(gems: List[str], intention: str, task: str) -> str:
    """Prompt template for designing Ruby software architecture grounded in containerized gem AST and decode graphs."""
    gem_str = ", ".join(f"`{g}`" for g in gems)
    return f"""You are a Principal Ruby Systems Architect designing software grounded in the actual codebase AST and documentation of {gem_str}.

### Project Intention
{intention}

### Target Task
{task}

### Instructions for Grounded Architecture:
1. Call `query_gem_graphrag(gems={gems!r}, query="{task}", intention="{intention}")` to retrieve exact AST method signatures, decode parameter types, and verified code examples.
2. Ground your architecture strictly in the returned signatures and examples. Do not hallucinate gem APIs or method names.
3. Show concrete code snippets utilizing the verified `@parameter` and `@returns` types.
4. Highlight architectural relationships: module nesting, class inheritance, and call paths.
"""


# --- CLI & Daemon Lifecycle Management ---


def _is_port_in_use(host: str, port: int) -> bool:
    """Check if a network port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _read_pid(pid_file: Path) -> Optional[int]:
    """Read PID from PID file, or None if invalid/missing."""
    if not pid_file.exists():
        return None
    try:
        content = pid_file.read_text().strip()
        return int(content) if content else None
    except (ValueError, OSError):
        return None


def _is_process_running(pid: int) -> bool:
    """Check if process with given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def daemon_start(host: str, port: int, pid_file: Path, log_file: Path) -> int:
    """Start background SSE server daemon process."""
    pid = _read_pid(pid_file)
    if pid is not None and _is_process_running(pid):
        print(f"RubyGemDB MCP server is already running (PID: {pid}).")
        return 0

    if pid_file.exists():
        pid_file.unlink(missing_ok=True)

    if _is_port_in_use(host, port):
        print(f"Error: Port {port} is already in use by another process.", file=sys.stderr)
        return 1

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    log_handle = open(log_file, "a", encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "rubygemdb.mcp_server",
        "--foreground",
        "--host",
        host,
        "--port",
        str(port),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    info_file = pid_file.with_suffix(".json")
    pid_file.write_text(str(proc.pid))
    try:
        info_file.write_text(json.dumps({"pid": proc.pid, "host": host, "port": port}))
    except OSError:
        pass
    time.sleep(0.3)

    if proc.poll() is not None:
        print("Error: Background process terminated immediately. Check log:", log_file, file=sys.stderr)
        pid_file.unlink(missing_ok=True)
        info_file.unlink(missing_ok=True)
        return 1

    print(f"Started RubyGemDB MCP server (PID: {proc.pid}) on http://{host}:{port}/sse")
    print(f"Log file: {log_file}")
    return 0


def daemon_status(host: str, port: int, pid_file: Path, log_file: Path) -> int:
    """Report status of the background MCP server daemon."""
    info_file = pid_file.with_suffix(".json")
    if info_file.exists():
        try:
            data = json.loads(info_file.read_text())
            host = data.get("host", host)
            port = data.get("port", port)
        except Exception:
            pass

    pid = _read_pid(pid_file)
    if pid is None:
        print("RubyGemDB MCP server is stopped.")
        return 1

    if _is_process_running(pid):
        print(f"RubyGemDB MCP server is running (PID: {pid}) on http://{host}:{port}/sse")
        print(f"Log file: {log_file}")
        return 0

    print(f"RubyGemDB MCP server is stopped (stale PID file: {pid}).")
    pid_file.unlink(missing_ok=True)
    info_file.unlink(missing_ok=True)
    return 1


def daemon_stop(pid_file: Path) -> int:
    """Stop the background MCP server daemon cleanly."""
    info_file = pid_file.with_suffix(".json")
    pid = _read_pid(pid_file)
    if pid is None:
        print("RubyGemDB MCP server is not running.")
        info_file.unlink(missing_ok=True)
        return 0

    if not _is_process_running(pid):
        print(f"RubyGemDB MCP server was not running (cleaned up stale PID: {pid}).")
        pid_file.unlink(missing_ok=True)
        info_file.unlink(missing_ok=True)
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.1)
            if not _is_process_running(pid):
                break
        else:
            os.kill(pid, signal.SIGKILL)
        print(f"Stopped RubyGemDB MCP server (PID: {pid}).")
    except OSError as e:
        print(f"Error terminating process {pid}: {e}", file=sys.stderr)
    finally:
        pid_file.unlink(missing_ok=True)
        info_file.unlink(missing_ok=True)

    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for rubygemdb-mcp."""
    parser = argparse.ArgumentParser(
        prog="rubygemdb-mcp",
        description="RubyGemDB Model Context Protocol (MCP) Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  rubygemdb-mcp                        # Run stdio MCP server (for Claude Code, Antigravity, Cursor)
  rubygemdb-mcp --daemon start         # Run background SSE daemon on port 8000
  rubygemdb-mcp --daemon status        # Check status of background daemon
  rubygemdb-mcp --daemon stop          # Stop background daemon
  rubygemdb-mcp --daemon start --port 8011  # Run background SSE daemon on port 8011
  rubygemdb-mcp --sse                  # Start background SSE daemon (shortcut)
        """,
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport protocol (stdio or sse; default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for SSE server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE server (default: 8000)",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Start background SSE daemon (shortcut for --daemon start)",
    )
    parser.add_argument(
        "--daemon",
        choices=["start", "status", "stop"],
        help="Manage background SSE daemon lifecycle (start, status, stop)",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run SSE server in foreground (used by worker or for debugging)",
    )
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=None,
        help="Path to PID file (default: data/mcp.pid)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Path to log file (default: data/mcp.log)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for rubygemdb-mcp."""
    args = parse_args(argv)

    pid_file = args.pid_file or (settings.data_dir / "mcp.pid")
    log_file = args.log_file or (settings.data_dir / "mcp.log")

    # If --sse is given without --daemon and without --foreground, treat as --daemon start
    daemon_action = args.daemon
    if args.sse and not daemon_action and not args.foreground:
        daemon_action = "start"

    # Handle daemon lifecycle commands
    if daemon_action == "start":
        return daemon_start(args.host, args.port, pid_file, log_file)
    elif daemon_action == "status":
        return daemon_status(args.host, args.port, pid_file, log_file)
    elif daemon_action == "stop":
        return daemon_stop(pid_file)

    # Run server
    if args.foreground or args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
        return 0
    else:
        # Default: stdio transport
        mcp.run(transport="stdio")
        return 0


if __name__ == "__main__":
    sys.exit(main())
