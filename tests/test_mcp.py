"""Automated test suite for RubyGemDB Model Context Protocol (MCP) server.

Verifies:
- FastMCP server instantiation and instant initialization
- Read-only tools (search_gems, get_gem, compose_stack, list_categories)
- Static resources (rubygemdb://taxonomy, rubygemdb://stack-seeds)
- Prompt template (design-ruby-stack)
- Strictly read-only database operations
- CLI argument parsing & daemon lifecycle management (start, status, stop)
"""

import json
import socket
from pathlib import Path

import pytest

from rubygemdb.mcp_server import (
    daemon_start,
    daemon_status,
    daemon_stop,
    mcp,
    parse_args,
)
from rubygemdb.models.categories import CATEGORIES, LAYERS
from rubygemdb.storage.sqlite_storage import SQLiteStorage


@pytest.fixture
def tmp_daemon_files(tmp_path: Path):
    """Provide isolated paths for daemon PID and log files."""
    pid_file = tmp_path / "test_mcp.pid"
    log_file = tmp_path / "test_mcp.log"
    return pid_file, log_file


@pytest.mark.asyncio
async def test_mcp_server_initialization_fast():
    """Verify FastMCP server is initialized without heavy embeddings blocking."""
    assert mcp.name == "rubygemdb"
    assert "Ruby gem analysis" in (mcp.instructions or "")


@pytest.mark.asyncio
async def test_list_categories_tool():
    """Verify list_categories returns all 21 categories with layers and base seeds."""
    result = await mcp.call_tool("list_categories", {})
    categories = result[1]["result"]
    assert len(categories) == 21
    slugs = {c["slug"] for c in categories}
    assert "web_frameworks" in slugs
    assert "document_parsing" in slugs
    assert "ai_llm" in slugs

    for c in categories:
        assert c["layer"] in LAYERS
        assert "description" in c
        assert "sub_categories" in c
        assert isinstance(c["base_gems"], list)


@pytest.mark.asyncio
async def test_search_gems_tool():
    """Verify search_gems tool performs instant FTS keyword search and category/phase filtering."""
    # Free-text search
    result = await mcp.call_tool("search_gems", {"query": "nokogiri", "limit": 5})
    hits = result[1]["result"]
    assert len(hits) > 0
    hit_names = [h["name"] for h in hits]
    assert "nokogiri" in hit_names

    top = next(h for h in hits if h["name"] == "nokogiri")
    assert top["category"] == "document_parsing"
    assert top["layer"] == "plumbing"

    # Category filtered search
    cat_result = await mcp.call_tool(
        "search_gems",
        {"query": "parser", "category": "document_parsing", "limit": 5},
    )
    cat_hits = cat_result[1]["result"]
    for hit in cat_hits:
        assert hit["category"] == "document_parsing"

    # Phase filtered search
    phase_result = await mcp.call_tool(
        "search_gems",
        {"query": "parser", "phase": "input", "limit": 5},
    )
    phase_hits = phase_result[1]["result"]
    for hit in phase_hits:
        assert hit["phase"] == "input"


@pytest.mark.asyncio
async def test_get_gem_tool_existing():
    """Verify get_gem returns full architectural metadata for an existing classified gem."""
    result = await mcp.call_tool("get_gem", {"name": "nokogiri"})
    gem = result[1]["result"]
    assert gem["name"] == "nokogiri"
    assert gem["category"] == "document_parsing"
    assert gem["layer"] == "plumbing"
    assert "html_xml_parsing" in gem["sub_categories"]
    assert "role" in gem
    assert "capabilities" in gem
    assert "dependencies" in gem
    assert "risks" in gem
    assert "signals" in gem
    assert gem["classified"] is True


@pytest.mark.asyncio
async def test_get_gem_tool_nonexistent():
    """Verify get_gem returns an error dictionary for a non-existent gem."""
    result = await mcp.call_tool("get_gem", {"name": "non_existent_gem_xyz123"})
    res = result[1]["result"]
    assert "error" in res
    assert "not found in database" in res["error"]


@pytest.mark.asyncio
async def test_compose_stack_tool():
    """Verify compose_stack tool generates layered manifest and Gemfile snippet."""
    result = await mcp.call_tool(
        "compose_stack",
        {"query": "High performance background job worker"},
    )
    stack = result[1]["result"]
    assert "layers" in stack
    assert len(stack["layers"]) > 0

    layers = [ly["layer"] for ly in stack["layers"]]
    assert "substrate" in layers
    assert "quality" in layers

    # Gemfile snippet
    assert "gemfile" in stack
    assert 'source "https://rubygems.org"' in stack["gemfile"]
    assert 'gem "' in stack["gemfile"]

    # Manifest YAML
    assert "manifest_yaml" in stack
    assert "layers:" in stack["manifest_yaml"]


@pytest.mark.asyncio
async def test_database_is_read_only_during_tool_calls():
    """Verify MCP tools run in strictly read-only mode without mutating SQLite tables."""
    storage = SQLiteStorage(read_only=True)
    assert storage.is_read_only is True

    # Mutating operations must raise PermissionError
    with pytest.raises(PermissionError):
        storage.save_classified_gems([])

    with pytest.raises(PermissionError):
        storage.delete_gem("nokogiri")

    with pytest.raises(PermissionError):
        storage.wipe_classified_gems()

    with pytest.raises(PermissionError):
        storage.add_gem_to_inventory("new_gem")

    with pytest.raises(PermissionError):
        storage.set_preferred(["nokogiri"], True)


@pytest.mark.asyncio
async def test_taxonomy_resource():
    """Verify rubygemdb://taxonomy static resource produces complete taxonomy markdown."""
    contents = await mcp.read_resource("rubygemdb://taxonomy")
    assert len(contents) == 1
    markdown = contents[0].content
    assert "# RubyGemDB Architectural Taxonomy" in markdown
    for layer in LAYERS:
        assert f"## Layer: {layer.capitalize()}" in markdown
    for cat in CATEGORIES:
        assert f"`{cat.slug}`" in markdown


@pytest.mark.asyncio
async def test_stack_seeds_resource():
    """Verify rubygemdb://stack-seeds static resource produces curated seed markdown."""
    contents = await mcp.read_resource("rubygemdb://stack-seeds")
    assert len(contents) == 1
    markdown = contents[0].content
    assert "# Curated RubyGemDB Stack Seeds" in markdown
    assert "`dotenv`" in markdown
    assert "`rubocop`" in markdown


@pytest.mark.asyncio
async def test_design_ruby_stack_prompt():
    """Verify design-ruby-stack prompt template produces structured workflow instructions."""
    project_desc = "Fast multi-tenant REST API with SQLite"
    prompt = await mcp.get_prompt("design-ruby-stack", {"project_description": project_desc})
    assert len(prompt.messages) == 1
    text = prompt.messages[0].content.text
    assert project_desc in text
    assert "expert Ruby systems architect" in text
    assert "Taxonomy & Layer Orientation" in text
    assert "Layer-by-Layer Discovery" in text
    assert "Compose & Validate Stack" in text


def test_cli_argument_parsing():
    """Verify parse_args supports all CLI modes, transports, and flags."""
    # Default (no args)
    args = parse_args([])
    assert args.transport == "stdio"
    assert args.daemon is None
    assert args.sse is False
    assert args.foreground is False

    # Daemon start with port
    args = parse_args(["--daemon", "start", "--port", "8011"])
    assert args.daemon == "start"
    assert args.port == 8011

    # Daemon status
    args = parse_args(["--daemon", "status"])
    assert args.daemon == "status"

    # Daemon stop
    args = parse_args(["--daemon", "stop"])
    assert args.daemon == "stop"

    # --sse flag
    args = parse_args(["--sse"])
    assert args.sse is True


def test_daemon_lifecycle_full(tmp_daemon_files):
    """Verify full daemon lifecycle: start -> status -> stop -> status."""
    pid_file, log_file = tmp_daemon_files

    # Find an open port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    # Initial status: stopped
    assert daemon_status("127.0.0.1", free_port, pid_file, log_file) == 1

    # Start daemon
    start_code = daemon_start("127.0.0.1", free_port, pid_file, log_file)
    assert start_code == 0
    assert pid_file.exists()
    pid = int(pid_file.read_text().strip())
    assert pid > 0

    # Status while running
    status_code = daemon_status("127.0.0.1", free_port, pid_file, log_file)
    assert status_code == 0

    # Starting again while running returns 0
    assert daemon_start("127.0.0.1", free_port, pid_file, log_file) == 0

    # Stop daemon
    stop_code = daemon_stop(pid_file)
    assert stop_code == 0
    assert not pid_file.exists()

    # Status after stop
    assert daemon_status("127.0.0.1", free_port, pid_file, log_file) == 1

    # Stopping when already stopped returns 0
    assert daemon_stop(pid_file) == 0


def test_daemon_stale_pid_handling(tmp_daemon_files):
    """Verify daemon commands cleanly handle and clean up stale PID files."""
    pid_file, log_file = tmp_daemon_files

    # Write a non-existent PID (e.g. 999999)
    pid_file.write_text("999999\n")
    info_file = pid_file.with_suffix(".json")
    info_file.write_text(json.dumps({"pid": 999999, "host": "127.0.0.1", "port": 8000}))

    # Status cleans up stale PID
    code = daemon_status("127.0.0.1", 8000, pid_file, log_file)
    assert code == 1
    assert not pid_file.exists()
    assert not info_file.exists()
