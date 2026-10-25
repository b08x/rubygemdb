"""Automated unit and integration tests for GitHub starred gems import pipeline.

Covers:
- GitHub starred repository fetching (pagination, language filter, token resolution)
- Gem resolution (direct match, *.gemspec root search, non-gem skip, Context7 ID)
- Deduplication and enrichment against SQLite inventory
- SQLite schema migration and Pydantic models with genai_uses column
- GenAI backend tooling role inference (heuristics + LLM fallback)
- Prune audit, report generation, and deletion safety gates
- CLI subcommand flags (--dry-run, --classify, --embed, --apply-prune, --prune-list)
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rubygemdb.cli import run_cli
from rubygemdb.core.config import settings
from rubygemdb.models.gem import GemClassification, GemEntry, GemInventoryItem
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.services.github_stars import GitHubStarsService
from rubygemdb.services.import_stars_pipeline import ImportStarsPipeline
from rubygemdb.services.llm import LLMService
from rubygemdb.services.prune import PruneCandidate, PruneReport, PruneService
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.storage.sqlite_storage import SQLiteStorage


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_import_stars.sqlite"
    storage = SQLiteStorage(db_path=str(db_file))
    return storage, db_file


# -----------------------------------------------------------------------------
# 1. Schema & Pydantic Models with genai_uses
# -----------------------------------------------------------------------------

def test_models_genai_uses_field():
    """Verify GemEntry and GemInventoryItem models serialize and deserialize genai_uses."""
    inv = GemInventoryItem(
        name="test-gem",
        version="1.0.0",
        description="A test gem",
        genai_uses="Document Chunking & Token Preprocessing for RAG",
    )
    assert inv.genai_uses == "Document Chunking & Token Preprocessing for RAG"
    dumped = inv.model_dump()
    assert dumped["genai_uses"] == "Document Chunking & Token Preprocessing for RAG"

    entry = GemEntry(
        name="test-gem",
        classification=GemClassification(primary="ai_llm"),
        genai_uses="Agent Harness & Multi-Agent Orchestration",
    )
    assert entry.genai_uses == "Agent Harness & Multi-Agent Orchestration"
    assert entry.model_dump()["genai_uses"] == "Agent Harness & Multi-Agent Orchestration"


def test_sqlite_schema_migration_genai_uses(tmp_path: Path):
    """Verify automatic SQLite migration adds genai_uses to legacy databases."""
    legacy_db = tmp_path / "legacy.sqlite"
    # Create legacy schema without genai_uses
    with sqlite3.connect(legacy_db) as conn:
        conn.execute("""
            CREATE TABLE inventory (
                name TEXT PRIMARY KEY,
                version TEXT,
                category TEXT,
                description TEXT,
                homepage TEXT,
                source_code_uri TEXT,
                context7_id TEXT,
                verified INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE classified_gems (
                name TEXT PRIMARY KEY,
                classification TEXT,
                role TEXT,
                capabilities TEXT,
                risks TEXT,
                signals TEXT,
                dependencies TEXT,
                description TEXT
            )
        """)
        conn.commit()

    # Initializing SQLiteStorage triggers automatic migration
    storage = SQLiteStorage(db_path=str(legacy_db))
    with sqlite3.connect(legacy_db) as conn:
        inv_cols = {row[1] for row in conn.execute("PRAGMA table_info(inventory)")}
        cls_cols = {row[1] for row in conn.execute("PRAGMA table_info(classified_gems)")}
        assert "genai_uses" in inv_cols
        assert "genai_uses" in cls_cols

    # Verify upsert and retrieval with genai_uses
    storage.upsert_inventory_gem(
        name="foo-gem",
        version="0.1.0",
        description="Desc",
        genai_uses="Agent Memory",
    )
    item = storage.get_inventory_gem("foo-gem")
    assert item is not None
    assert item["genai_uses"] == "Agent Memory"


# -----------------------------------------------------------------------------
# 2. GitHub Starred Repositories Service
# -----------------------------------------------------------------------------

def test_github_token_resolution(monkeypatch):
    """Verify token resolution checks settings, env, and gh CLI fallback."""
    monkeypatch.setattr(settings, "github_token", "token-from-settings")
    assert GitHubStarsService._resolve_token() == "token-from-settings"

    monkeypatch.setattr(settings, "github_token", None)
    monkeypatch.setenv("GITHUB_TOKEN", "token-from-env")
    assert GitHubStarsService._resolve_token() == "token-from-env"

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "token-from-gh-env")
    assert GitHubStarsService._resolve_token() == "token-from-gh-env"

    monkeypatch.delenv("GH_TOKEN", raising=False)
    # Mock subprocess.run for gh auth token
    mock_run = MagicMock()
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "token-from-gh-cli\n"
    with patch("rubygemdb.services.github_stars.shutil.which", return_value="/bin/gh"), \
         patch("rubygemdb.services.github_stars.subprocess.run", mock_run):
        assert GitHubStarsService._resolve_token() == "token-from-gh-cli"


def test_github_fetch_starred_repos_pagination_and_filter():
    """Verify starred repos fetching with pagination and language filtering."""
    service = GitHubStarsService(token="mock-token")

    page1 = [
        {"name": "repo1", "language": "Ruby"},
        {"name": "repo2", "language": "Python"},
    ] * 50  # 100 items
    page2 = [
        {"name": "repo3", "language": "Ruby"},
        {"name": "repo4", "language": "JavaScript"},
    ]  # 2 items, terminates pagination

    with patch("requests.get") as mock_get:
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: page1),
            MagicMock(status_code=200, json=lambda: page2),
        ]
        results = service.fetch_starred_repos(user="testuser", language="Ruby")
        # page1 has 50 Ruby repos, page2 has 1 Ruby repo -> 51 total
        assert len(results) == 51
        assert all(r["language"] == "Ruby" for r in results)
        assert mock_get.call_count == 2


def test_gem_resolution_direct_match():
    """Verify repo matching directly by repo name on rubygems.org."""
    mock_rg = MagicMock(spec=RubyGemsService)
    mock_rg.fetch_gem_info.side_effect = lambda name: {
        "name": name,
        "version": "1.2.3",
        "info": "Baran gem description",
        "homepage_uri": "https://rubygems.org/gems/baran",
        "source_code_uri": "https://github.com/moeki0/baran",
    } if name == "baran" else None

    service = GitHubStarsService(rubygems_service=mock_rg)
    repo = {
        "name": "baran",
        "owner": {"login": "moeki0"},
        "html_url": "https://github.com/moeki0/baran",
        "description": "Text Splitter for LLM datasets",
        "stargazers_count": 25,
    }

    gem = service.resolve_gem_for_repo(repo)
    assert gem is not None
    assert gem["name"] == "baran"
    assert gem["version"] == "1.2.3"
    assert gem["context7_id"] == "/moeki0/baran"
    assert gem["stars"] == 25


def test_gem_resolution_gemspec_fallback():
    """Verify repo matching via root *.gemspec inspection when repo name differs."""
    mock_rg = MagicMock(spec=RubyGemsService)
    # Repo name "my-awesome-tool" is not on rubygems, but gemspec "awesome-core" is
    mock_rg.fetch_gem_info.side_effect = lambda name: {
        "name": "awesome-core",
        "version": "2.0.0",
        "info": "Awesome Core engine",
        "homepage_uri": "https://awesome.org",
        "source_code_uri": "https://github.com/org/my-awesome-tool",
    } if name == "awesome-core" else None

    service = GitHubStarsService(rubygems_service=mock_rg)
    # Mock repo root contents returning an awesome-core.gemspec
    service.fetch_repo_contents = MagicMock(return_value=[
        {"name": "README.md", "type": "file"},
        {"name": "awesome-core.gemspec", "type": "file"},
    ])

    repo = {
        "name": "my-awesome-tool",
        "owner": {"login": "org"},
        "html_url": "https://github.com/org/my-awesome-tool",
        "description": "Tool repo",
    }

    gem = service.resolve_gem_for_repo(repo)
    assert gem is not None
    assert gem["name"] == "awesome-core"
    assert gem["version"] == "2.0.0"
    assert gem["context7_id"] == "/org/my-awesome-tool"


def test_gem_resolution_non_gem_skipped():
    """Verify non-gem repository is skipped gracefully."""
    mock_rg = MagicMock(spec=RubyGemsService)
    mock_rg.fetch_gem_info.return_value = None

    service = GitHubStarsService(rubygems_service=mock_rg)
    service.fetch_repo_contents = MagicMock(return_value=[{"name": "app.rb"}])

    repo = {
        "name": "standalone-script",
        "owner": {"login": "user"},
        "html_url": "https://github.com/user/standalone-script",
    }
    assert service.resolve_gem_for_repo(repo) is None


# -----------------------------------------------------------------------------
# 3. GenAI Backend Tooling Role Inference
# -----------------------------------------------------------------------------

def test_genai_uses_inference_heuristics():
    """Verify heuristic inference rules for GenAI backend tooling roles."""
    rg = MagicMock(spec=RubyGemsService)
    llm = MagicMock(spec=LLMService)
    llm.infer_genai_uses.return_value = None  # test heuristics fallback

    classifier = GemClassifier(rubygems_service=rg, llm_service=llm)

    # Chunking / segmentation
    use1 = classifier.infer_genai_tooling_uses("baran", "Text Splitter for LLM datasets", "document_parsing", use_llm=False)
    assert "Document Chunking" in use1

    # Prompt tooling
    use2 = classifier.infer_genai_tooling_uses("prompt-builder", "Prompt template engine", "ai_llm", ["prompt_tooling"], use_llm=False)
    assert "Prompt Engineering" in use2

    # MCP tooling
    use3 = classifier.infer_genai_tooling_uses("ruby-mcp", "Model Context Protocol client", "mcp_tooling", use_llm=False)
    assert "MCP" in use3

    # Evaluation
    use4 = classifier.infer_genai_tooling_uses("rag-eval", "Benchmark and metric evaluation", "testing_qa", use_llm=False)
    assert "Evaluation" in use4

    # Vector / similarity
    use5 = classifier.infer_genai_tooling_uses("ruby-hnsw", "HNSW vector index", "ai_llm", ["embeddings_vector"], use_llm=False)
    assert "Vector" in use5

    # Structured schema validation
    use6 = classifier.infer_genai_tooling_uses("dry-schema", "Validation contracts", "runtime_validation", use_llm=False)
    assert "Structured Tool Output Validation" in use6


def test_genai_uses_inference_llm_override():
    """Verify LLM output takes precedence when available and confident."""
    rg = MagicMock(spec=RubyGemsService)
    llm = MagicMock(spec=LLMService)
    llm.infer_genai_uses.return_value = "Custom Specialized Agent Memory Storage Engine"

    classifier = GemClassifier(rubygems_service=rg, llm_service=llm)
    use = classifier.infer_genai_tooling_uses("custom-mem", "A cache for agents", "persistence", use_llm=True)
    assert use == "Custom Specialized Agent Memory Storage Engine"
    llm.infer_genai_uses.assert_called_once()


# -----------------------------------------------------------------------------
# 4. Deduplication & Inventory Enrichment Pipeline
# -----------------------------------------------------------------------------

def test_pipeline_deduplication_and_enrichment(temp_db):
    """Verify that existing gems are enriched without duplicates, and new gems are added."""
    storage, _ = temp_db

    # Pre-populate SQLite with an existing gem missing homepage, context7_id, and genai_uses
    storage.upsert_inventory_gem(
        name="existing-gem",
        version="0.5.0",
        description="Original description",
        homepage=None,
        source_code_uri="https://github.com/org/existing-gem",
        context7_id=None,
        genai_uses=None,
    )

    mock_stars = MagicMock(spec=GitHubStarsService)
    mock_stars.fetch_starred_repos.return_value = [
        {"name": "existing-gem", "language": "Ruby"},
        {"name": "brand-new-gem", "language": "Ruby"},
        {"name": "non-gem-repo", "language": "Ruby"},
    ]

    mock_stars.resolve_gem_for_repo.side_effect = lambda repo, **kw: {
        "existing-gem": {
            "name": "existing-gem",
            "version": "0.5.0",
            "description": "Original description",
            "homepage": "https://existing-gem.org",
            "source_code_uri": "https://github.com/org/existing-gem",
            "context7_id": "/org/existing-gem",
        },
        "brand-new-gem": {
            "name": "brand-new-gem",
            "version": "1.0.0",
            "description": "Brand new AI gem",
            "homepage": "https://brandnew.org",
            "source_code_uri": "https://github.com/org/brand-new-gem",
            "context7_id": "/org/brand-new-gem",
        },
    }.get(repo["name"])

    mock_pruner = MagicMock(spec=PruneService)
    mock_report = PruneReport()
    mock_pruner.build_report.return_value = mock_report
    mock_pruner.write_report.return_value = "data/prune-report.md"

    pipeline = ImportStarsPipeline(
        storage=storage,
        stars_service=mock_stars,
        prune_service=mock_pruner,
        dry_run=False,
    )

    res = pipeline.run()

    assert res["discovered_count"] == 3
    assert len(res["resolved_gems"]) == 2
    assert len(res["skipped_repos"]) == 1
    assert len(res["new_gems"]) == 1
    assert len(res["enriched_gems"]) == 1

    # Verify existing-gem was backfilled in SQLite without creating duplicate records
    inv_gems = storage.get_all_inventory_gems()
    assert len(inv_gems) == 2
    by_name = {g["name"]: g for g in inv_gems}

    existing_record = by_name["existing-gem"]
    assert existing_record["homepage"] == "https://existing-gem.org"
    assert existing_record["context7_id"] == "/org/existing-gem"
    assert existing_record["genai_uses"] is not None

    new_record = by_name["brand-new-gem"]
    assert new_record["version"] == "1.0.0"
    assert new_record["genai_uses"] is not None


# -----------------------------------------------------------------------------
# 5. Prune Audit & Deletion Safety Gates
# -----------------------------------------------------------------------------

def test_pipeline_prune_safety_by_default(temp_db):
    """Verify that by default, prune candidates are NEVER deleted without explicit flags."""
    storage, _ = temp_db
    storage.upsert_inventory_gem(name="stale-gem", description="Stale")
    storage.upsert_inventory_gem(name="active-gem", description="Active")

    mock_stars = MagicMock(spec=GitHubStarsService)
    mock_stars.fetch_starred_repos.return_value = []

    mock_pruner = MagicMock(spec=PruneService)
    report = PruneReport(stale=[PruneCandidate(name="stale-gem", reason="stale: 4 years")])
    mock_pruner.build_report.return_value = report
    mock_pruner.write_report.return_value = "data/prune-report.md"

    # Run without --apply-prune
    pipeline = ImportStarsPipeline(
        storage=storage,
        stars_service=mock_stars,
        prune_service=mock_pruner,
        dry_run=False,
        apply_prune=False,
    )
    res = pipeline.run()

    assert len(res["pruned_gems"]) == 0
    # Both gems must remain in storage
    assert len(storage.get_all_inventory_gems()) == 2


def test_pipeline_apply_prune_and_prune_list(temp_db, tmp_path: Path):
    """Verify --apply-prune and --prune-list safely remove only designated candidates."""
    storage, _ = temp_db
    storage.upsert_inventory_gem(name="stale-1", description="S1")
    storage.upsert_inventory_gem(name="stale-2", description="S2")
    storage.upsert_inventory_gem(name="active", description="Active")

    mock_stars = MagicMock(spec=GitHubStarsService)
    mock_stars.fetch_starred_repos.return_value = []

    mock_pruner = MagicMock(spec=PruneService)
    report = PruneReport(stale=[
        PruneCandidate(name="stale-1", reason="stale"),
        PruneCandidate(name="stale-2", reason="stale"),
    ])
    mock_pruner.build_report.return_value = report

    # Test --prune-list: subset approval (stale-1 only)
    prune_file = tmp_path / "prune.txt"
    prune_file.write_text("# Comments allowed\nstale-1\n")

    pipeline = ImportStarsPipeline(
        storage=storage,
        stars_service=mock_stars,
        prune_service=mock_pruner,
        prune_list=str(prune_file),
    )
    res = pipeline.run()

    assert res["pruned_gems"] == ["stale-1"]
    remaining = {g["name"] for g in storage.get_all_inventory_gems()}
    assert remaining == {"stale-2", "active"}

    # Test --apply-prune removes all report candidates
    pipeline2 = ImportStarsPipeline(
        storage=storage,
        stars_service=mock_stars,
        prune_service=mock_pruner,
        apply_prune=True,
    )
    res2 = pipeline2.run()
    assert "stale-2" in res2["pruned_gems"]
    assert {g["name"] for g in storage.get_all_inventory_gems()} == {"active"}


# -----------------------------------------------------------------------------
# 6. Dry Run Guarantee
# -----------------------------------------------------------------------------

def test_pipeline_dry_run_zero_writes(temp_db, tmp_path: Path):
    """Verify --dry-run guarantees zero database mutations and zero file writes."""
    storage, db_file = temp_db
    storage.upsert_inventory_gem(name="pre-existing", description="Pre-existing")

    mock_stars = MagicMock(spec=GitHubStarsService)
    mock_stars.fetch_starred_repos.return_value = [{"name": "new-gem", "language": "Ruby"}]
    mock_stars.resolve_gem_for_repo.return_value = {
        "name": "new-gem",
        "description": "A new gem",
        "homepage": "https://new.org",
        "source_code_uri": "https://github.com/org/new-gem",
    }

    mock_pruner = MagicMock(spec=PruneService)
    mock_pruner.build_report.return_value = PruneReport(
        stale=[PruneCandidate(name="pre-existing", reason="stale")]
    )

    pipeline = ImportStarsPipeline(
        storage=storage,
        stars_service=mock_stars,
        prune_service=mock_pruner,
        dry_run=True,
        apply_prune=True,
        classify=True,
        embed=True,
    )

    res = pipeline.run()

    # Dry-run reports what it would do
    assert len(res["new_gems"]) == 1
    assert len(res["pruned_gems"]) == 1
    assert res["prune_report_file"] is None

    # SQLite must remain unmodified!
    inv = storage.get_all_inventory_gems()
    assert len(inv) == 1
    assert inv[0]["name"] == "pre-existing"
    mock_pruner.write_report.assert_not_called()


# -----------------------------------------------------------------------------
# 7. CLI Subcommand Integration
# -----------------------------------------------------------------------------

def test_cli_import_stars_invocation(monkeypatch, capsys):
    """Verify `rubygemdb import-stars --dry-run` invokes the pipeline and prints tables."""
    mock_pipeline = MagicMock()
    mock_res = {
        "discovered_count": 10,
        "resolved_gems": [{"name": "gem1"}],
        "skipped_repos": [{"name": "repo2"}],
        "new_gems": [{"name": "gem1", "version": "1.0.0", "genai_uses": "Agent Memory"}],
        "enriched_gems": [],
        "unchanged_gems": [],
        "prune_report": PruneReport(),
        "pruned_gems": [],
        "classified_gems": [],
        "prune_report_file": None,
    }
    mock_pipeline.return_value.run.return_value = mock_res

    monkeypatch.setattr(sys, "argv", ["rubygemdb", "import-stars", "--user", "b08x", "--dry-run"])

    with patch("rubygemdb.cli.ImportStarsPipeline", mock_pipeline):
        run_cli()

    captured = capsys.readouterr().out
    assert "Import Summary for @b08x (DRY RUN)" in captured
    assert "Repositories Discovered" in captured
    assert "10" in captured
    assert "Dry run completed." in captured
