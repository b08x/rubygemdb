"""Automated tests for the rubygemdb agent skill and installer.

Verifies:
1. Installer deployment (install_skill and rubygemdb install-skill) in copy and link modes.
2. Repo-local gem.py subcommands (search, info, stack, categories, prefer/unprefer, stats,
   graphrag build/query, sessions list/show/export/delete).
3. SKILL.md synchronization (3-tier routing, 11 MCP tools, resources, prompts, operator CLI,
   OPENROUTER_API_KEY, and absence of DEVSTRAL_API_KEY).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
from typing import Any
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".skills" / "rubygemdb"


def load_module(name: str, relative_path: str) -> Any:
    """Dynamically load a module from the repo-local .skills directory."""
    import sys

    script_path = SKILL_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gem_mod() -> Any:
    return load_module("skill_gem", "scripts/gem.py")


@pytest.fixture
def installer_mod() -> Any:
    return load_module("skill_installer", "install.py")


@pytest.fixture
def isolated_dbs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gem_mod: Any) -> tuple[Path, Path]:
    """Create isolated catalog and session SQLite databases."""
    catalog_db = tmp_path / "catalog.sqlite"
    sessions_db = tmp_path / "sessions.sqlite"

    with sqlite3.connect(catalog_db) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE inventory (
                name TEXT PRIMARY KEY,
                version TEXT,
                category TEXT,
                description TEXT,
                homepage TEXT,
                source_code_uri TEXT,
                context7_id TEXT,
                verified INTEGER DEFAULT 0,
                preferred INTEGER DEFAULT 0,
                genai_uses TEXT
            );
        """)
        cursor.execute("""
            CREATE TABLE classified_gems (
                name TEXT PRIMARY KEY,
                classification TEXT,
                role TEXT,
                capabilities TEXT,
                risks TEXT,
                signals TEXT,
                dependencies TEXT,
                description TEXT,
                genai_uses TEXT,
                sub_categories TEXT DEFAULT '[]'
            );
        """)
        cursor.execute("CREATE VIRTUAL TABLE gem_fts USING fts5(name, description, category);")

        # Insert sample gem: nokogiri
        cursor.execute(
            """
            INSERT INTO inventory (name, version, category, description, homepage, source_code_uri, context7_id, verified, preferred, genai_uses)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "nokogiri",
                "1.15.0",
                "document_parsing",
                "HTML and XML parser for Ruby",
                "https://nokogiri.org",
                "https://github.com/sparklemotion/nokogiri",
                "/sparklemotion/nokogiri",
                1,
                0,
                "Parsing HTML/XML in document pipelines",
            ),
        )
        cursor.execute(
            """
            INSERT INTO classified_gems (name, classification, role, capabilities, risks, signals, dependencies, description, genai_uses, sub_categories)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "nokogiri",
                json.dumps({
                    "primary": "document_parsing",
                    "confidence": 0.85,
                    "sub_categories": ["html_xml_parsing"],
                }),
                json.dumps({"description": "XML/HTML parser", "attaches_to": "document"}),
                json.dumps(["parse_html", "xpath"]),
                json.dumps({"invasiveness": 2, "coupling": 1, "abstraction_leak": "low"}),
                json.dumps({"rails": False, "external_io": True, "native_ext": True}),
                json.dumps(["racc"]),
                "HTML and XML parser for Ruby",
                "Parsing HTML/XML in document pipelines",
                json.dumps(["html_xml_parsing"]),
            ),
        )
        cursor.execute(
            "INSERT INTO gem_fts (name, description, category) VALUES (?, ?, ?)",
            ("nokogiri", "HTML and XML parser for Ruby", "document_parsing"),
        )

        # Insert sample gem: thor
        cursor.execute(
            """
            INSERT INTO inventory (name, version, category, description, homepage, source_code_uri, context7_id, verified, preferred, genai_uses)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thor",
                "1.3.0",
                "cli_libraries",
                "Toolkit for building powerful command-line interfaces",
                "http://whatisthor.com",
                "https://github.com/rails/thor",
                "/rails/thor",
                1,
                1,
                "CLI command framework",
            ),
        )
        cursor.execute(
            """
            INSERT INTO classified_gems (name, classification, role, capabilities, risks, signals, dependencies, description, genai_uses, sub_categories)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thor",
                json.dumps({
                    "primary": "cli_libraries",
                    "confidence": 0.90,
                    "sub_categories": ["cli_frameworks"],
                }),
                json.dumps({"description": "CLI framework", "attaches_to": "cli"}),
                json.dumps(["subcommands", "options"]),
                json.dumps({"invasiveness": 1, "coupling": 1, "abstraction_leak": "low"}),
                json.dumps({"rails": False, "external_io": False, "native_ext": False}),
                json.dumps([]),
                "Toolkit for building powerful command-line interfaces",
                "CLI command framework",
                json.dumps(["cli_frameworks"]),
            ),
        )
        cursor.execute(
            "INSERT INTO gem_fts (name, description, category) VALUES (?, ?, ?)",
            ("thor", "Toolkit for building powerful command-line interfaces", "cli_libraries"),
        )
        conn.commit()

    monkeypatch.setattr(gem_mod, "DB_PATH", str(catalog_db))
    monkeypatch.setattr(gem_mod, "SESSION_DB_PATH", str(sessions_db))
    from rubygemdb.core.config import settings

    monkeypatch.setattr(settings, "sqlite_db_file", catalog_db)
    monkeypatch.setattr(settings, "session_db_file", sessions_db)

    return catalog_db, sessions_db


# --- Installer Tests ---


def test_installer_copy_mode(tmp_path: Path, installer_mod: Any):
    target_dir = tmp_path / "skills"
    dest = installer_mod.install_skill(target_dir=target_dir, link=False)

    assert dest == target_dir / "rubygemdb"
    assert not dest.is_symlink()
    assert dest.is_dir()

    skill_md = dest / "SKILL.md"
    gem_py = dest / "scripts" / "gem.py"
    install_py = dest / "install.py"

    assert skill_md.exists()
    assert gem_py.exists()
    assert install_py.exists()
    assert gem_py.stat().st_mode & 0o111 != 0  # Executable permissions

    # Verify idempotency by calling again
    dest2 = installer_mod.install_skill(target_dir=target_dir, link=False)
    assert dest2 == dest
    assert skill_md.exists()


def test_installer_link_mode(tmp_path: Path, installer_mod: Any):
    target_dir = tmp_path / "skills"
    dest = installer_mod.install_skill(target_dir=target_dir, link=True)

    assert dest == target_dir / "rubygemdb"
    assert dest.is_symlink()
    assert dest.resolve() == SKILL_DIR.resolve()


def test_cli_install_skill_subcommand(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    from rubygemdb.cli import run_install_skill

    target_dir = tmp_path / "cli_skills"
    args = argparse.Namespace(target=str(target_dir), link=False)
    run_install_skill(args)

    captured = capsys.readouterr()
    assert "Installed rubygemdb skill to" in captured.out
    assert (target_dir / "rubygemdb" / "SKILL.md").exists()
    assert (target_dir / "rubygemdb" / "scripts" / "gem.py").exists()


# --- SKILL.md Verification Tests ---


def test_skill_md_content_and_synchronization():
    skill_md_path = SKILL_DIR / "SKILL.md"
    assert skill_md_path.exists()
    content = skill_md_path.read_text(encoding="utf-8")

    # Frontmatter and naming
    assert "name: rubygemdb" in content
    assert "description:" in content

    # 3-Tier Routing Model
    assert "3-Tier Routing Architecture" in content
    assert "Tier 1: rubygemdb-mcp" in content
    assert "Tier 2: Skill Script" in content
    assert "Tier 3: Repository Operator CLI" in content

    # All 11 MCP Tools
    expected_tools = [
        "search_gems",
        "get_gem",
        "compose_stack",
        "list_categories",
        "build_gem_graphrag",
        "query_gem_graphrag",
        "list_rag_sessions",
        "get_rag_session",
        "resume_rag_session",
        "export_rag_session_transcript",
        "delete_rag_session",
    ]
    for tool in expected_tools:
        assert tool in content, f"Tool '{tool}' must be documented in SKILL.md"

    # MCP Resources
    assert "rubygemdb://taxonomy" in content
    assert "rubygemdb://stack-seeds" in content

    # MCP Prompts
    assert "design-ruby-stack" in content
    assert "gem-architect-rag" in content

    # Background SSE Daemon
    assert "--daemon start" in content
    assert "--daemon status" in content
    assert "--daemon stop" in content

    # Containerized GraphRAG & [C1] Citation Contract
    assert "rubygemdb-indexer:latest" in content
    assert "[C1]" in content
    assert "### Citations" in content

    # Operator CLI commands
    assert "import-stars" in content
    assert "prune" in content
    assert "reclassify" in content
    assert "cache" in content
    assert "install-skill" in content

    # Environment variables
    assert "OPENROUTER_API_KEY" in content
    assert "DEVSTRAL_API_KEY" not in content, "Stale DEVSTRAL_API_KEY must not appear in SKILL.md"
    assert "CONTEXT7_API_KEY" in content
    assert "QUERY_EXPANSION_ENABLED" in content
    assert "RERANKER_ENABLED" in content


# --- gem.py Subcommand Tests ---


def test_gem_search_category_and_json(gem_mod: Any, isolated_dbs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]):
    # Search with category match
    exit_code = gem_mod.main(["search", "HTML", "--category", "document_parsing", "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert len(results) == 1
    assert results[0]["name"] == "nokogiri"
    assert results[0]["category"] == "document_parsing"

    # Search with non-matching category
    exit_code = gem_mod.main(["search", "HTML", "--category", "cli_libraries", "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert len(results) == 0


def test_gem_search_expand_and_rerank_flags(
    gem_mod: Any,
    isolated_dbs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from rubygemdb.services import hybrid_search as hs_mod

    called_kwargs: dict[str, Any] = {}
    original_hybrid_search = hs_mod.hybrid_search

    def mock_hs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        called_kwargs.update(kwargs)
        return original_hybrid_search(*args, **kwargs)

    monkeypatch.setattr(hs_mod, "hybrid_search", mock_hs)
    monkeypatch.setattr(gem_mod, "hybrid_search", mock_hs)

    exit_code = gem_mod.main(["search", "interfaces", "--expand", "--rerank", "--json"])
    assert exit_code == 0
    assert called_kwargs.get("query_expander") is not None
    assert called_kwargs.get("query_expander").is_enabled is True
    assert called_kwargs.get("reranker") is not None
    assert called_kwargs.get("reranker").is_enabled is True


def test_gem_info_subcommand(gem_mod: Any, isolated_dbs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]):
    # JSON mode
    exit_code = gem_mod.main(["info", "nokogiri", "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    rec = json.loads(captured.out)
    assert rec["name"] == "nokogiri"
    assert rec["category"] == "document_parsing"
    assert rec["sub_categories"] == ["html_xml_parsing"]
    assert rec["genai_uses"] == "Parsing HTML/XML in document pipelines"
    assert rec["layer"] == "plumbing"
    assert rec["phase"] == "input"

    # Text mode
    exit_code = gem_mod.main(["info", "nokogiri"])
    assert exit_code == 0
    text_out = capsys.readouterr().out
    assert "nokogiri — document_parsing" in text_out
    assert "sub-categories: html_xml_parsing" in text_out
    assert "genai-uses: Parsing HTML/XML in document pipelines" in text_out

    # Unknown gem
    exit_code = gem_mod.main(["info", "nonexistent_gem_xyz"])
    assert exit_code == 1


def test_gem_categories_subcommand(gem_mod: Any, capsys: pytest.CaptureFixture[str]):
    # Summary mode JSON
    exit_code = gem_mod.main(["categories", "--json"])
    assert exit_code == 0
    cats = json.loads(capsys.readouterr().out)
    assert len(cats) == 21
    for c in cats:
        assert "slug" in c
        assert "sub_categories" in c
        assert isinstance(c["sub_categories"], list)

    # Specific category slug JSON
    exit_code = gem_mod.main(["categories", "ai_llm", "--json"])
    assert exit_code == 0
    ai_cat = json.loads(capsys.readouterr().out)
    assert ai_cat["slug"] == "ai_llm"
    assert "llm_clients" in ai_cat["sub_categories"]
    assert "ruby_llm" in ai_cat["sub_categories"]["llm_clients"]

    # Specific category slug Text
    exit_code = gem_mod.main(["categories", "ai_llm"])
    assert exit_code == 0
    text_out = capsys.readouterr().out
    assert "ai_llm — AI & LLM" in text_out
    assert "sub-categories:" in text_out
    assert "llm_clients" in text_out

    # Unknown slug
    exit_code = gem_mod.main(["categories", "invalid_slug_123"])
    assert exit_code == 1


def test_gem_stack_max_gems(
    gem_mod: Any,
    isolated_dbs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from rubygemdb.services.stack import StackService

    build_stack_args: dict[str, Any] = {}

    def mock_build_stack(self: Any, query: str, max_gems: int = 10, **kwargs: Any) -> dict[str, Any]:
        build_stack_args["query"] = query
        build_stack_args["max_gems"] = max_gems
        return {
            "context": query,
            "layers": [
                {
                    "layer": "composition",
                    "entries": [
                        {
                            "gem": "thor",
                            "category": "cli_libraries",
                            "layer": "composition",
                            "role": "CLI framework",
                            "source": "match",
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(StackService, "build_stack", mock_build_stack)

    exit_code = gem_mod.main(["stack", "CLI tool", "--max-gems", "4"])
    assert exit_code == 0
    assert build_stack_args["max_gems"] == 4
    output = capsys.readouterr().out
    assert 'gem "thor"' in output


def test_gem_prefer_unprefer_preferred(
    gem_mod: Any,
    isolated_dbs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
):
    # Prefer nokogiri
    exit_code = gem_mod.main(["prefer", "nokogiri"])
    assert exit_code == 0
    capsys.readouterr()

    # List preferred
    exit_code = gem_mod.main(["preferred"])
    assert exit_code == 0
    preferred_out = capsys.readouterr().out
    assert "nokogiri" in preferred_out

    # Unprefer nokogiri
    exit_code = gem_mod.main(["unprefer", "nokogiri"])
    assert exit_code == 0
    capsys.readouterr()

    # Verify nokogiri is no longer preferred
    exit_code = gem_mod.main(["preferred"])
    assert exit_code == 0
    preferred_after = capsys.readouterr().out
    assert "nokogiri" not in preferred_after


def test_gem_stats_subcommand(gem_mod: Any, isolated_dbs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]):
    exit_code = gem_mod.main(["stats", "--json"])
    assert exit_code == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["total_classified"] == 2
    assert "by_layer" in stats
    assert "by_category" in stats


def test_gem_schema_migration(tmp_path: Path, gem_mod: Any):
    older_db = tmp_path / "older.sqlite"
    with sqlite3.connect(older_db) as conn:
        conn.execute("CREATE TABLE inventory (name TEXT PRIMARY KEY, version TEXT, category TEXT);")
        conn.execute("CREATE TABLE classified_gems (name TEXT PRIMARY KEY, classification TEXT);")
        conn.commit()

    gem_mod._ensure_schema_columns(str(older_db))

    with sqlite3.connect(older_db) as conn:
        inv_cols = {row[1] for row in conn.execute("PRAGMA table_info(inventory)")}
        assert "preferred" in inv_cols
        assert "genai_uses" in inv_cols

        cls_cols = {row[1] for row in conn.execute("PRAGMA table_info(classified_gems)")}
        assert "sub_categories" in cls_cols
        assert "genai_uses" in cls_cols


def test_gem_graphrag_build_and_query(
    gem_mod: Any,
    isolated_dbs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    dummy_citations = [
        {
            "id": "C1",
            "gem": "thor",
            "file": "lib/thor.rb",
            "node_id": "method:Thor#start",
            "node_type": "method",
            "symbol": "Thor#start",
            "signature": "def start(given_args=ARGV, config={})",
            "summary": "Start the command line interface.",
        }
    ]

    class MockGraphRAG:
        active_gems = ["thor"]
        active_hash = "mockhash1234"
        nodes_by_id = {"method:Thor#start": {"id": "method:Thor#start"}}

        def build_or_load_index(self, gems: list[str], force_rebuild: bool = False) -> dict[str, Any]:
            self.active_gems = gems
            return {"version": "1.0", "edges": [{"source": "a", "target": "b"}]}

        def retrieve(self, query: str, top_k: int = 5) -> dict[str, Any]:
            return {
                "primary_nodes": [{"id": "method:Thor#start"}],
                "expanded_nodes": [],
                "edges": [],
            }

        def format_grounded_context(self, retrieval: dict[str, Any], intention: str = "") -> str:
            retrieval["citations"] = dummy_citations
            return (
                "### Grounded AST Symbols\n"
                "- **[C1] Method `Thor#start`** (Gem: `thor` | File: `lib/thor.rb`)\n"
                "  - Signature: `def start(given_args=ARGV, config={})`\n"
            )

    monkeypatch.setattr("rubygemdb.services.graphrag.GraphRAGService", MockGraphRAG)

    # 1. Test build
    exit_code = gem_mod.main(["graphrag", "build", "thor", "--intention", "Test CLI", "--json"])
    assert exit_code == 0
    build_out = json.loads(capsys.readouterr().out)
    assert build_out["status"] == "ready"
    assert build_out["gems"] == ["thor"]
    assert "session_id" in build_out
    sid = build_out["session_id"]

    # 2. Test query
    exit_code = gem_mod.main([
        "graphrag", "query", "How to run thor?",
        "--gems", "thor",
        "--session-id", sid,
        "--json",
    ])
    assert exit_code == 0
    query_out = json.loads(capsys.readouterr().out)
    assert query_out["session_id"] == sid
    assert len(query_out["citations"]) == 1
    assert query_out["citations"][0]["id"] == "C1"
    assert "[C1]" in query_out["grounded_context"]

    # Verify session messages were persisted in SessionStorage
    session_storage = gem_mod._get_session_storage()
    messages = session_storage.get_messages(sid)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "How to run thor?"
    assert messages[1].role == "agent"
    assert len(messages[1].citations) == 1
    assert messages[1].citations[0]["id"] == "C1"


def test_gem_sessions_lifecycle(
    gem_mod: Any,
    isolated_dbs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
):
    session_storage = gem_mod._get_session_storage()
    session = session_storage.create_session(
        title="Test Session",
        active_gems=["thor", "nokogiri"],
        intention="Parsing XML and CLI",
    )
    sid = session.session_id

    session_storage.append_message(sid, role="user", content="Hello world")
    session_storage.append_message(
        sid,
        role="agent",
        content="Response with citation [C1]",
        citations=[{"id": "C1", "gem": "thor", "file": "lib/thor.rb", "symbol": "Thor#start"}],
    )

    # 1. sessions list
    exit_code = gem_mod.main(["sessions", "list", "--json"])
    assert exit_code == 0
    sessions_list = json.loads(capsys.readouterr().out)
    assert any(s["session_id"] == sid for s in sessions_list)

    # 2. sessions show
    exit_code = gem_mod.main(["sessions", "show", sid, "--json"])
    assert exit_code == 0
    show_data = json.loads(capsys.readouterr().out)
    assert show_data["session"]["session_id"] == sid
    assert len(show_data["messages"]) == 2

    # 3. sessions export (markdown)
    exit_code = gem_mod.main(["sessions", "export", sid, "--format", "markdown"])
    assert exit_code == 0
    md_transcript = capsys.readouterr().out
    assert f"**Session ID:** `{sid}`" in md_transcript
    assert "Response with citation [C1]" in md_transcript

    # 4. sessions export (json)
    exit_code = gem_mod.main(["sessions", "export", sid, "--format", "json"])
    assert exit_code == 0
    json_transcript = json.loads(capsys.readouterr().out)
    assert json_transcript["session_id"] == sid

    # 5. sessions delete
    exit_code = gem_mod.main(["sessions", "delete", sid, "--json"])
    assert exit_code == 0
    delete_res = json.loads(capsys.readouterr().out)
    assert delete_res["deleted"] is True

    # 6. sessions show after delete
    exit_code = gem_mod.main(["sessions", "show", sid, "--json"])
    assert exit_code == 1
    assert "not found" in capsys.readouterr().out
