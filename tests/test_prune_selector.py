"""Automated tests for CLI multi-selector and prune command.

Verifies:
- Candidate item formatting (PruneCandidate, tuple, dict, string)
- Gum choose subprocess invocation and output parsing
- Textual SelectionList TUI operation and bindings
- Non-interactive terminal fallbacks
- rubygemdb prune CLI command flags (--all, --select, --dry-run, --prune-list, --output-list)
- rubygemdb reclassify and import-stars integration with --select-prune
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rubygemdb.cli import run_cli
from rubygemdb.services.prune import (
    PruneCandidate,
    PruneReport,
    parse_prune_candidates,
    parse_prune_file,
)
from rubygemdb.storage.sqlite_storage import SQLiteStorage
from rubygemdb.ui.selector import (
    PruneSelectorApp,
    format_candidate_item,
    select_gems_to_prune,
)


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_prune.sqlite"
    storage = SQLiteStorage(db_path=str(db_file))
    return storage, db_file


# -----------------------------------------------------------------------------
# 1. Candidate Item Formatting
# -----------------------------------------------------------------------------

def test_format_candidate_item():
    """Verify various candidate formats produce uniform (name, label) tuples."""
    # From PruneCandidate
    c1 = PruneCandidate(name="gem-a", reason="stale: 3 years", detail="last release 2021-01-01")
    name1, label1 = format_candidate_item(c1)
    assert name1 == "gem-a"
    assert "gem-a — stale: 3 years (last release 2021-01-01)" == label1

    # From tuple
    name2, label2 = format_candidate_item(("gem-b", "overlap", "kept 'gem-c'"))
    assert name2 == "gem-b"
    assert "gem-b — overlap (kept 'gem-c')" == label2

    # From dict
    name3, label3 = format_candidate_item({"name": "gem-c", "reason": "stale", "detail": "old"})
    assert name3 == "gem-c"
    assert "gem-c — stale (old)" == label3

    # From raw string
    name4, label4 = format_candidate_item("gem-d")
    assert name4 == "gem-d"
    assert label4 == "gem-d"


# -----------------------------------------------------------------------------
# 2. Gum Choose Engine
# -----------------------------------------------------------------------------

def test_select_with_gum_success():
    """Verify gum choose executes with proper arguments and parses selected lines."""
    items = [("gem-1", "gem-1 (stale)"), ("gem-2", "gem-2 (overlap)")]

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "gem-1\ngem-2\n"

    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        from rubygemdb.ui.selector import _select_with_gum

        res = _select_with_gum(items, header="Select:", preselected=["gem-1"])
        assert res == ["gem-1", "gem-2"]

        # Verify command flags
        cmd_called = mock_run.call_args[0][0]
        assert "gum" in cmd_called
        assert "choose" in cmd_called
        assert "--no-limit" in cmd_called
        assert "--label-delimiter=||" in cmd_called
        assert "--header=Select:" in cmd_called
        assert "--selected=gem-1" in cmd_called

        # Verify command arguments contain candidate options
        assert "gem-1 (stale)||gem-1" in cmd_called
        assert "gem-2 (overlap)||gem-2" in cmd_called
        assert mock_run.call_args[1].get("stdout") is not None


def test_select_with_gum_cancelled():
    """Verify non-zero exit code (user Esc / Ctrl+C) returns empty list."""
    items = [("gem-1", "gem-1")]
    mock_proc = MagicMock()
    mock_proc.returncode = 130  # user cancelled

    with patch("subprocess.run", return_value=mock_proc):
        from rubygemdb.ui.selector import _select_with_gum

        assert _select_with_gum(items) == []


# -----------------------------------------------------------------------------
# 3. Textual SelectionList Engine
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_textual_prune_selector_app():
    """Verify Textual PruneSelectorApp interactions (select, confirm, cancel)."""
    items = [
        ("gem-x", "gem-x (stale: 4y)"),
        ("gem-y", "gem-y (overlap)"),
    ]

    # Test confirmation with preselected
    app = PruneSelectorApp(items=items, title="Test Prune", preselected=["gem-x"])
    async with app.run_test() as pilot:
        # Check initial state
        sl = app.query_one("#selection-list")
        assert getattr(sl, "selected", []) == ["gem-x"]

        # Select all
        app.action_select_all()
        assert set(getattr(sl, "selected", [])) == {"gem-x", "gem-y"}

        # Clear all
        app.action_clear_all()
        assert len(getattr(sl, "selected", [])) == 0

        # Select all again and confirm with enter key
        app.action_select_all()
        await pilot.press("enter")

    assert set(app.return_value or []) == {"gem-x", "gem-y"}


# -----------------------------------------------------------------------------
# 4. Top-level select_gems_to_prune Dispatcher
# -----------------------------------------------------------------------------

def test_select_gems_to_prune_empty():
    assert select_gems_to_prune([]) == []


def test_select_gems_to_prune_non_interactive(monkeypatch):
    """When sys.stdin is not a tty, returns preselected without prompting."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    candidates = [PruneCandidate(name="gem-1", reason="stale")]
    assert select_gems_to_prune(candidates, preselected=["gem-1"]) == ["gem-1"]
    assert select_gems_to_prune(candidates, preselected=None) == []


def test_select_gems_to_prune_backend_dispatch(monkeypatch):
    """Verify backend argument dispatches to gum or textual."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    candidates = [PruneCandidate(name="gem-1", reason="stale")]

    with patch("rubygemdb.ui.selector._select_with_gum", return_value=["gem-1"]) as mock_gum:
        res = select_gems_to_prune(candidates, backend="gum")
        assert res == ["gem-1"]
        mock_gum.assert_called_once()

    with patch("rubygemdb.ui.selector._select_with_textual", return_value=["gem-1"]) as mock_textual:
        res2 = select_gems_to_prune(candidates, backend="textual")
        assert res2 == ["gem-1"]
        mock_textual.assert_called_once()


# -----------------------------------------------------------------------------
# 5. CLI `rubygemdb prune` Subcommand
# -----------------------------------------------------------------------------

def test_cli_prune_empty_catalog(monkeypatch, capsys):
    """When no prune candidates exist, prints clean message and exits."""
    mock_storage = MagicMock()
    mock_storage.get_all_inventory_gems.return_value = [{"name": "active-gem"}]
    mock_storage.rubygems = MagicMock()

    mock_pruner = MagicMock()
    mock_pruner.build_report.return_value = PruneReport()

    monkeypatch.setattr(sys, "argv", ["rubygemdb", "prune"])
    with patch("rubygemdb.cli.SQLiteStorage", return_value=mock_storage), \
         patch("rubygemdb.cli.PruneService", return_value=mock_pruner):
        run_cli()

    captured = capsys.readouterr().out
    assert "No prune candidates found" in captured


def test_cli_prune_interactive_selection(monkeypatch, capsys):
    """Verify `rubygemdb prune` launches multi-selector and prunes chosen gems."""
    mock_storage = MagicMock()
    inventory = [{"name": "stale-gem"}, {"name": "active-gem"}]
    mock_storage.get_all_inventory_gems.return_value = inventory
    mock_storage.rubygems = MagicMock()

    mock_pruner = MagicMock()
    mock_pruner.build_report.return_value = PruneReport(
        stale=[PruneCandidate(name="stale-gem", reason="stale: 3 years")]
    )

    # Mock user selecting 'stale-gem' in multi-selector
    monkeypatch.setattr(sys, "argv", ["rubygemdb", "prune", "--select"])
    with patch("rubygemdb.cli.SQLiteStorage", return_value=mock_storage), \
         patch("rubygemdb.cli.PruneService", return_value=mock_pruner), \
         patch("rubygemdb.cli.select_gems_to_prune", return_value=["stale-gem"]) as mock_select:
        run_cli()

        mock_select.assert_called_once()
        mock_storage.delete_gem.assert_called_once_with("stale-gem")

    captured = capsys.readouterr().out
    assert "Successfully pruned 1 gems" in captured


def test_cli_prune_dry_run_and_output_list(monkeypatch, capsys, tmp_path: Path):
    """Verify --dry-run prevents deletions, and --output-list saves chosen gems."""
    mock_storage = MagicMock()
    inventory = [{"name": "stale-1"}, {"name": "stale-2"}]
    mock_storage.get_all_inventory_gems.return_value = inventory
    mock_storage.rubygems = MagicMock()

    mock_pruner = MagicMock()
    mock_pruner.build_report.return_value = PruneReport(
        stale=[
            PruneCandidate(name="stale-1", reason="stale"),
            PruneCandidate(name="stale-2", reason="stale"),
        ]
    )

    out_file = tmp_path / "chosen-prune.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        ["rubygemdb", "prune", "--all", "--dry-run", "--output-list", str(out_file)],
    )

    with patch("rubygemdb.cli.SQLiteStorage", return_value=mock_storage), \
         patch("rubygemdb.cli.PruneService", return_value=mock_pruner):
        run_cli()

    # Dry-run guarantees zero deletions!
    mock_storage.delete_gem.assert_not_called()

    # Output list file was written
    assert out_file.exists()
    saved = out_file.read_text().splitlines()
    assert saved == ["stale-1", "stale-2"]

    captured = capsys.readouterr().out
    assert "DRY RUN" in captured
    assert "Database unmodified" in captured


def test_cli_prune_prune_list_flag(monkeypatch, capsys, tmp_path: Path):
    """Verify `rubygemdb prune --prune-list` prunes gems from file."""
    mock_storage = MagicMock()
    mock_storage.get_all_inventory_gems.return_value = [{"name": "stale-1"}, {"name": "stale-2"}]
    mock_storage.rubygems = MagicMock()

    mock_pruner = MagicMock()
    mock_pruner.build_report.return_value = PruneReport(
        stale=[PruneCandidate(name="stale-1", reason="stale"), PruneCandidate(name="stale-2", reason="stale")]
    )

    prune_file = tmp_path / "list.txt"
    prune_file.write_text("stale-1\n")

    monkeypatch.setattr(sys, "argv", ["rubygemdb", "prune", "--prune-list", str(prune_file)])
    with patch("rubygemdb.cli.SQLiteStorage", return_value=mock_storage), \
         patch("rubygemdb.cli.PruneService", return_value=mock_pruner):
        run_cli()

        mock_storage.delete_gem.assert_called_once_with("stale-1")


# -----------------------------------------------------------------------------
# 6. `reclassify --select-prune` Integration
# -----------------------------------------------------------------------------

def test_cli_reclassify_select_prune(monkeypatch, capsys, tmp_path: Path):
    """Verify `rubygemdb reclassify --select-prune` launches multi-selector."""
    mock_storage = MagicMock()
    mock_storage.get_all_inventory_gems.return_value = [{"name": "stale-gem"}, {"name": "active"}]
    mock_storage.rubygems = MagicMock()
    mock_storage.load_classified_gems.return_value = []

    mock_pruner = MagicMock()
    mock_pruner.build_report.return_value = PruneReport(
        stale=[PruneCandidate(name="stale-gem", reason="stale")]
    )
    mock_pruner.write_report.return_value = "report.md"

    mock_classifier = MagicMock()
    mock_entry = MagicMock()
    mock_entry.classification.primary = "testing_qa"
    mock_entry.model_dump.return_value = {"name": "active", "category": "testing_qa"}
    mock_classifier.return_value.classify.return_value = mock_entry

    out_dir = tmp_path / "reclassify_out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["rubygemdb", "reclassify", "--select-prune", "--out", str(out_dir)],
    )

    with patch("rubygemdb.cli.SQLiteStorage", return_value=mock_storage), \
         patch("rubygemdb.cli.PruneService", return_value=mock_pruner), \
         patch("rubygemdb.cli.select_gems_to_prune", return_value=["stale-gem"]) as mock_select, \
         patch("rubygemdb.cli.JSONStorage"), \
         patch("rubygemdb.cli.GemClassifier", mock_classifier), \
         patch("rubygemdb.cli.shutil.copy2"):
        run_cli()

        mock_select.assert_called_once()
        mock_storage.delete_gem.assert_called_once_with("stale-gem")

    captured = capsys.readouterr().out
    assert "Pruned 1 gems via multi-selector" in captured


# -----------------------------------------------------------------------------
# 7. `import-stars --select-prune` Integration
# -----------------------------------------------------------------------------

def test_pipeline_select_prune(temp_db):
    """Verify ImportStarsPipeline with select_prune launches multi-selector."""
    storage, _ = temp_db
    storage.upsert_inventory_gem(name="old-gem", description="Old")

    mock_stars = MagicMock()
    mock_stars.fetch_starred_repos.return_value = []

    mock_pruner = MagicMock()
    mock_pruner.build_report.return_value = PruneReport(
        stale=[PruneCandidate(name="old-gem", reason="stale")]
    )
    mock_pruner.write_report.return_value = "data/prune-report.md"

    from rubygemdb.services.import_stars_pipeline import ImportStarsPipeline

    pipeline = ImportStarsPipeline(
        storage=storage,
        stars_service=mock_stars,
        prune_service=mock_pruner,
        select_prune=True,
        dry_run=False,
    )

    with patch("rubygemdb.ui.selector.select_gems_to_prune", return_value=["old-gem"]) as mock_select:
        res = pipeline.run()
        mock_select.assert_called_once()
        assert res["pruned_gems"] == ["old-gem"]
        assert len(storage.get_all_inventory_gems()) == 0


# -----------------------------------------------------------------------------
# 8. Prune File Parsing & Positional CLI Arguments
# -----------------------------------------------------------------------------

def test_parse_prune_file_markdown_table():
    """Verify parsing gem names and metadata from markdown report tables."""
    md_content = """# Prune Report
Generated: 2026-10-25

## Stale gems

| Gem | Detail |
|---|---|
| ansi_palette | stale: no release in 11 years (last release 2015-09-13) |
| aubio | stale: no release in 6 years (last release 2020-09-21) |

## Overlap prunables
| Gem | Reason | Detail |
|---|---|---|
| google_drive | overlap: normalized name shared | kept 'google-drive-ruby' |
"""
    cands = parse_prune_candidates(md_content)
    assert len(cands) == 3
    assert cands[0].name == "ansi_palette"
    assert "stale: no release in 11 years" in cands[0].reason
    assert cands[1].name == "aubio"
    assert cands[2].name == "google_drive"
    assert cands[2].detail == "kept 'google-drive-ruby'"

    names = parse_prune_file(md_content)
    assert names == ["ansi_palette", "aubio", "google_drive"]


def test_parse_prune_file_plain_and_lists():
    """Verify parsing plain text with comments and markdown list formats."""
    text_content = """# Plain text list
gem_one # inline comment
gem_two
- gem_three — stale gem
* **gem_four**
1. gem_five, gem_six
"""
    names = parse_prune_file(text_content)
    assert names == ["gem_one", "gem_two", "gem_three", "gem_four", "gem_five", "gem_six"]


def test_cli_prune_positional_file(monkeypatch, capsys, tmp_path: Path):
    """Verify `rubygemdb prune report.md` parses file and accepts --all / --select."""
    mock_storage = MagicMock()
    mock_storage.get_all_inventory_gems.return_value = [{"name": "gem-a"}, {"name": "gem-b"}]

    report_file = tmp_path / "prune-report.md"
    report_file.write_text("""| Gem | Detail |\n|---|---|\n| gem-a | stale |\n""")

    monkeypatch.setattr(sys, "argv", ["rubygemdb", "prune", str(report_file), "--all"])
    with patch("rubygemdb.cli.SQLiteStorage", return_value=mock_storage):
        run_cli()

        mock_storage.delete_gem.assert_called_once_with("gem-a")

    captured = capsys.readouterr().out
    assert "Pruning from File" in captured
    assert "Successfully pruned 1 gems" in captured
