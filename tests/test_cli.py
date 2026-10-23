"""Automated tests for CLI cache subcommands and reclassify integration."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from rubygemdb.cli import run_cli
from rubygemdb.core.config import settings
from rubygemdb.services.embedding_cache import EmbeddingCache


@pytest.fixture
def temp_cache(tmp_path: Path, monkeypatch):
    cache_file = tmp_path / "cli_test_cache.sqlite"
    monkeypatch.setattr(settings, "embeddings_cache_file", cache_file)
    cache = EmbeddingCache(db_path=cache_file)
    return cache


def test_cli_cache_stats(temp_cache: EmbeddingCache, monkeypatch, capsys):
    temp_cache.put("gem one", "model-test", np.array([0.1, 0.2], dtype=np.float32))
    temp_cache.put("gem two", "model-test", np.array([0.3, 0.4], dtype=np.float32))

    monkeypatch.setattr(sys, "argv", ["rubygemdb", "cache", "stats"])
    run_cli()

    captured = capsys.readouterr().out
    assert "Universal Embedding Cache Statistics" in captured
    assert "Total Vectors" in captured
    assert "2" in captured
    assert "model-test" in captured


def test_cli_cache_clear(temp_cache: EmbeddingCache, monkeypatch, capsys):
    temp_cache.put("item 1", "model-a", np.array([1.0], dtype=np.float32))
    temp_cache.put("item 2", "model-b", np.array([2.0], dtype=np.float32))
    assert temp_cache.stats()["total_vectors"] == 2

    # Clear model-a
    monkeypatch.setattr(sys, "argv", ["rubygemdb", "cache", "clear", "--model", "model-a"])
    run_cli()

    captured = capsys.readouterr().out
    assert "Cleared 1 vector(s) for model 'model-a'" in captured
    assert temp_cache.stats()["total_vectors"] == 1

    # Clear remaining with --no-vacuum
    monkeypatch.setattr(sys, "argv", ["rubygemdb", "cache", "clear", "--no-vacuum"])
    run_cli()

    captured = capsys.readouterr().out
    assert "Cleared 1 vector(s) across all models" in captured
    assert temp_cache.stats()["total_vectors"] == 0


def test_cli_cache_prune(temp_cache: EmbeddingCache, monkeypatch, capsys):
    items = [(f"item {i}", np.array([float(i)], dtype=np.float32)) for i in range(10)]
    temp_cache.put_many(items, "model-p")
    assert temp_cache.stats()["total_vectors"] == 10

    # Prune to max 4 entries
    monkeypatch.setattr(
        sys, "argv", ["rubygemdb", "cache", "prune", "--max-entries", "4"]
    )
    run_cli()

    captured = capsys.readouterr().out
    assert "Embedding Cache Prune Summary" in captured
    assert "Capacity Vectors Trimmed" in captured
    assert temp_cache.stats()["total_vectors"] == 4


def test_cli_reclassify_clear_embeddings_cache_flag(temp_cache: EmbeddingCache, monkeypatch, tmp_path: Path):
    temp_cache.put("gem entry", "m", np.array([1.0], dtype=np.float32))
    assert temp_cache.stats()["total_vectors"] == 1

    # Mock the database/classification work in run_reclassify to isolate flag behavior
    mock_storage = MagicMock()
    mock_storage.get_all_inventory_gems.return_value = []
    mock_storage.rubygems = MagicMock()
    mock_storage.load_classified_gems.return_value = []

    mock_pruner = MagicMock()
    mock_report = MagicMock()
    mock_report.stale = []
    mock_report.overlaps = []
    mock_report.all_names = []
    mock_pruner.build_report.return_value = mock_report
    mock_pruner.write_report.return_value = "report.md"

    out_dir = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "rubygemdb", "reclassify",
        "--clear-embeddings-cache",
        "--apply-prune",
        "--out", str(out_dir)
    ])

    with patch("rubygemdb.cli.SQLiteStorage", return_value=mock_storage), \
         patch("rubygemdb.cli.PruneService", return_value=mock_pruner), \
         patch("rubygemdb.cli.JSONStorage"), \
         patch("rubygemdb.cli.GemClassifier"), \
         patch("rubygemdb.cli.shutil.copy2"):
        run_cli()

    # The flag should have cleared the cache
    assert temp_cache.stats()["total_vectors"] == 0
