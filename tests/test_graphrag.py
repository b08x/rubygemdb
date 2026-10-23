"""Automated tests for GraphRAGService indexing, 2-stage retrieval, and prompt context formatting."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rubygemdb.services.graphrag import GraphRAGService, compute_gem_set_hash


@pytest.fixture
def sample_graph_data():
    return {
        "version": "1.0",
        "gems": ["thor"],
        "nodes": [
            {
                "id": "gem:thor",
                "type": "gem",
                "name": "thor",
                "gem": "thor",
                "data": {},
            },
            {
                "id": "class:Thor",
                "type": "class",
                "name": "Thor",
                "gem": "thor",
                "data": {
                    "qualified_name": "Thor",
                    "superclass": "Object",
                    "docstring": "A toolkit for building powerful command-line interfaces.",
                },
            },
            {
                "id": "method:Thor#start",
                "type": "method",
                "name": "start",
                "gem": "thor",
                "data": {
                    "qualified_name": "Thor.start",
                    "signature": "def start(given_args = ARGV, config = {})",
                    "parameters": [{"name": "given_args"}, {"name": "config"}],
                    "docstring": "Start the Thor CLI application with the given arguments.",
                },
            },
            {
                "id": "doc_tag:Thor.start:parameter:given_args",
                "type": "doc_tag",
                "name": "parameter:given_args",
                "gem": "thor",
                "data": {
                    "directive": "parameter",
                    "name": "given_args",
                    "type": "Array(String)",
                    "text": "CLI argument tokens to parse.",
                    "target": "Thor.start",
                },
            },
            {
                "id": "example:Thor.start:1",
                "type": "example",
                "name": "Thor.start example 1",
                "gem": "thor",
                "data": {
                    "code": "MyCLI.start(ARGV)",
                    "target": "Thor.start",
                },
            },
        ],
        "edges": [
            {
                "source": "gem:thor",
                "target": "class:Thor",
                "type": "defines",
            },
            {
                "source": "class:Thor",
                "target": "method:Thor#start",
                "type": "defines",
            },
            {
                "source": "method:Thor#start",
                "target": "doc_tag:Thor.start:parameter:given_args",
                "type": "documents",
            },
            {
                "source": "method:Thor#start",
                "target": "example:Thor.start:1",
                "type": "documents",
            },
        ],
    }


def test_compute_gem_set_hash():
    h1 = compute_gem_set_hash(["Thor", "drydock"])
    h2 = compute_gem_set_hash(["drydock", "THOR"])
    assert h1 == h2
    assert len(h1) == 16


def test_build_or_load_index_fresh(tmp_path: Path, sample_graph_data):
    mock_fetcher = MagicMock()
    mock_fetcher.fetch_gem.return_value = tmp_path / "mock_thor"

    mock_runner = MagicMock()
    mock_runner.run_indexer.return_value = sample_graph_data

    mock_embeddings = MagicMock()

    service = GraphRAGService(
        gem_fetcher=mock_fetcher,
        container_runner=mock_runner,
        base_dir=tmp_path / "graphrag_cache",
        embeddings=mock_embeddings,
    )

    data = service.build_or_load_index(["thor"])
    assert data["version"] == "1.0"
    assert "method:Thor#start" in service.nodes_by_id
    assert len(service.edges_by_source["method:Thor#start"]) == 2

    mock_fetcher.fetch_gem.assert_called_once_with("thor")
    mock_runner.run_indexer.assert_called_once()
    mock_embeddings.index.assert_called_once()


def test_build_or_load_index_cached(tmp_path: Path, sample_graph_data):
    base_dir = tmp_path / "graphrag_cache"
    gem_hash = compute_gem_set_hash(["thor"])
    cache_dir = base_dir / gem_hash
    cache_dir.mkdir(parents=True)
    (cache_dir / "graph.json").write_text(json.dumps(sample_graph_data))
    (cache_dir / "txtai").mkdir()
    (cache_dir / "txtai" / "embeddings").touch()

    mock_fetcher = MagicMock()
    mock_runner = MagicMock()
    mock_embeddings = MagicMock()

    service = GraphRAGService(
        gem_fetcher=mock_fetcher,
        container_runner=mock_runner,
        base_dir=base_dir,
        embeddings=mock_embeddings,
    )

    data = service.build_or_load_index(["thor"])
    assert data["version"] == "1.0"
    assert "method:Thor#start" in service.nodes_by_id

    # Since it was cached, fetcher and container runner should NOT be called
    mock_fetcher.fetch_gem.assert_not_called()
    mock_runner.run_indexer.assert_not_called()


def test_build_or_load_index_ghost_cache_rebuilds(tmp_path: Path, sample_graph_data):
    """Ghost cache (graph.json exists and txtai dir exists, but embeddings missing) must rebuild."""
    base_dir = tmp_path / "graphrag_cache"
    gem_hash = compute_gem_set_hash(["thor"])
    cache_dir = base_dir / gem_hash
    cache_dir.mkdir(parents=True)
    (cache_dir / "graph.json").write_text(json.dumps(sample_graph_data))
    # txtai exists but empty - e.g. aborted run
    (cache_dir / "txtai").mkdir()

    mock_fetcher = MagicMock()
    mock_fetcher.fetch_gem.return_value = tmp_path / "mock_thor"

    mock_runner = MagicMock()
    mock_runner.run_indexer.return_value = sample_graph_data

    mock_embeddings = MagicMock()

    service = GraphRAGService(
        gem_fetcher=mock_fetcher,
        container_runner=mock_runner,
        base_dir=base_dir,
        embeddings=mock_embeddings,
    )

    data = service.build_or_load_index(["thor"])
    assert data["version"] == "1.0"
    # Must have triggered extraction because embeddings file was missing
    mock_fetcher.fetch_gem.assert_called_once_with("thor")
    mock_runner.run_indexer.assert_called_once()
    mock_embeddings.index.assert_called_once()


def test_two_stage_retrieval(tmp_path: Path, sample_graph_data):
    service = GraphRAGService(base_dir=tmp_path)
    service.graph_data = sample_graph_data
    service._index_graph_memory()

    # Query for "start" method with top_k=1 so primary node is the method
    result = service.retrieve("start CLI", top_k=1)

    primary_ids = [n["id"] for n in result["primary_nodes"]]
    assert "method:Thor#start" in primary_ids

    expanded_ids = [n["id"] for n in result["expanded_nodes"]]
    # 1-hop expansion discovers attached doc_tag, example, and owner class
    assert "doc_tag:Thor.start:parameter:given_args" in expanded_ids
    assert "example:Thor.start:1" in expanded_ids
    assert "class:Thor" in expanded_ids


def test_format_grounded_context(tmp_path: Path, sample_graph_data):
    service = GraphRAGService(base_dir=tmp_path)
    service.graph_data = sample_graph_data
    service.active_gems = ["thor"]
    service._index_graph_memory()

    retrieval = service.retrieve("start", top_k=2)
    context = service.format_grounded_context(retrieval, intention="Build a powerful CLI pipeline")

    assert "### Project Intention" in context
    assert "Build a powerful CLI pipeline" in context
    assert "### Active Indexed Gems" in context
    assert "Thor.start" in context
    assert "def start(given_args = ARGV, config = {})" in context
    assert "@parameter` `given_args` [Array(String)]" in context
    assert "MyCLI.start(ARGV)" in context
    assert "[C1]" in context
    assert "### Grounded Citations Index" in context
    assert "citations" in retrieval
    assert len(retrieval["citations"]) >= 2
    assert any(c["symbol"] == "Thor.start" for c in retrieval["citations"])


def test_get_citations(tmp_path: Path, sample_graph_data):
    service = GraphRAGService(base_dir=tmp_path)
    service.graph_data = sample_graph_data
    service.active_gems = ["thor"]
    service._index_graph_memory()

    retrieval = service.retrieve("start", top_k=2)
    citations = service.get_citations(retrieval)
    assert len(citations) >= 2
    assert citations[0]["id"] == "C1"
    assert citations[0]["gem"] == "thor"


def test_build_vector_index_chunk_progress(tmp_path: Path, sample_graph_data):
    mock_embeddings = MagicMock()
    # Ensure generator is consumed when index() is called
    mock_embeddings.index.side_effect = lambda gen: list(gen)

    service = GraphRAGService(base_dir=tmp_path, embeddings=mock_embeddings)
    service.graph_data = sample_graph_data
    service._index_graph_memory()

    progress_messages: list[str] = []
    service._build_vector_index(
        tmp_path / "txtai",
        progress_callback=progress_messages.append,
        chunk_size=2,
    )

    # 4 indexable nodes with chunk_size=2 -> 2 chunks
    assert any("Vectorizing chunk 1/2" in m for m in progress_messages)
    assert any("Vectorizing chunk 2/2" in m for m in progress_messages)
