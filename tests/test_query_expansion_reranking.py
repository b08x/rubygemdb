"""Automated tests for GGUF model manager, query expansion, cross-encoder reranking,
and their integration into hybrid search and GraphRAG.
"""

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rubygemdb.core.config import Settings
from rubygemdb.services.graphrag import GraphRAGService
from rubygemdb.services.hybrid_search import hybrid_search
from rubygemdb.services.model_manager import GGUFModelManager
from rubygemdb.services.query_expander import QueryExpander, QueryExpansion
from rubygemdb.services.reranker import CrossEncoderReranker


# -------------------------------------------------------------------------
# Step 2: Settings and Configuration
# -------------------------------------------------------------------------

def test_settings_defaults_and_env_aliases(monkeypatch, tmp_path):
    settings = Settings()
    assert settings.query_expansion_enabled is False
    assert settings.query_expander_repo_id == "mradermacher/qmd-query-expansion-1.7B-GGUF"
    assert settings.query_expander_filename == "qmd-query-expansion-1.7B.Q4_K_M.gguf"
    assert settings.query_expander_n_ctx == 1024
    assert settings.reranker_enabled is False
    assert settings.reranker_repo_id == "ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF"
    assert settings.reranker_filename == "qwen3-reranker-0.6b-q8_0.gguf"
    assert settings.reranker_n_ctx == 1024
    assert settings.reranker_candidate_multiplier == 3
    assert settings.resolved_models_dir == settings.data_dir / "models"

    # Test environment variable aliases
    monkeypatch.setenv("EXPANDER_ENABLED", "true")
    monkeypatch.setenv("EXPANDER_REPO_ID", "custom/expander-gguf")
    monkeypatch.setenv("CROSS_ENCODER_ENABLED", "true")
    monkeypatch.setenv("CROSS_ENCODER_FILENAME", "custom-reranker.gguf")
    monkeypatch.setenv("CANDIDATE_MULTIPLIER", "4")
    monkeypatch.setenv("MODELS_CACHE_DIR", str(tmp_path / "custom_models"))

    custom_settings = Settings()
    assert custom_settings.query_expansion_enabled is True
    assert custom_settings.query_expander_repo_id == "custom/expander-gguf"
    assert custom_settings.reranker_enabled is True
    assert custom_settings.reranker_filename == "custom-reranker.gguf"
    assert custom_settings.reranker_candidate_multiplier == 4
    assert custom_settings.resolved_models_dir == tmp_path / "custom_models"


# -------------------------------------------------------------------------
# Step 3: GGUF Model Manager
# -------------------------------------------------------------------------

def test_model_manager_download_and_caching(tmp_path):
    manager = GGUFModelManager(cache_dir=tmp_path)
    mock_file = tmp_path / "dummy.gguf"
    mock_file.write_text("dummy model content")

    with patch("huggingface_hub.hf_hub_download", return_value=str(mock_file)) as mock_download:
        # 1. Download
        path = manager.download_model("repo/test", "dummy.gguf")
        assert path == mock_file
        mock_download.assert_called_once_with(
            repo_id="repo/test",
            filename="dummy.gguf",
            local_dir=str(tmp_path),
        )

    # 2. Get Llama instance and verify caching
    mock_llama = MagicMock()
    with patch("llama_cpp.Llama", return_value=mock_llama) as mock_llama_cls:
        with patch.object(manager, "download_model", return_value=mock_file):
            inst1 = manager.get_llama("repo/test", "dummy.gguf", n_ctx=512)
            inst2 = manager.get_llama("repo/test", "dummy.gguf", n_ctx=512)

            assert inst1 is mock_llama
            assert inst2 is mock_llama
            assert mock_llama_cls.call_count == 1

        # Clear cache and verify re-instantiation
        manager.clear_cache()
        with patch.object(manager, "download_model", return_value=mock_file):
            manager.get_llama("repo/test", "dummy.gguf", n_ctx=512)
            assert mock_llama_cls.call_count == 2


def test_model_manager_graceful_fallback(tmp_path):
    manager = GGUFModelManager(cache_dir=tmp_path)

    # Network / Download failure
    with patch("huggingface_hub.hf_hub_download", side_effect=RuntimeError("Network offline")):
        path = manager.download_model("repo/fail", "fail.gguf")
        assert path is None

        # get_llama should return None without raising
        inst = manager.get_llama("repo/fail", "fail.gguf")
        assert inst is None

    # Llama load failure
    mock_file = tmp_path / "bad.gguf"
    mock_file.write_text("corrupted")
    with patch.object(manager, "download_model", return_value=mock_file):
        with patch("llama_cpp.Llama", side_effect=Exception("Corrupted weights")):
            inst = manager.get_llama("repo/bad", "bad.gguf")
            assert inst is None


# -------------------------------------------------------------------------
# Step 4: Query Expander
# -------------------------------------------------------------------------

def test_query_expander_prompt_and_parsing():
    expander = QueryExpander(enabled=True)

    raw_output = """
    - lex: full text search, sqlite fts5, bm25
    - vec: fast inverted index ruby library
    - vec: semantic document retrieval
    * lexical: text indexer
    """
    expansion = expander.parse_expansion(raw_output)
    assert "full text search" in expansion.lexical
    assert "sqlite fts5" in expansion.lexical
    assert "bm25" in expansion.lexical
    assert "text indexer" in expansion.lexical
    assert "fast inverted index ruby library" in expansion.vector
    assert "semantic document retrieval" in expansion.vector
    assert expansion.raw == raw_output


def test_query_expander_execution():
    mock_manager = MagicMock()
    mock_llama = MagicMock()
    mock_manager.get_expander_model.return_value = mock_llama

    mock_llama.create_completion.return_value = {
        "choices": [
            {
                "text": "lex: cli parser, terminal args\nvec: ruby command line interface builder\n"
            }
        ]
    }

    expander = QueryExpander(model_manager=mock_manager, enabled=True)
    res = expander.expand("cli tool")

    assert "cli parser" in res.lexical
    assert "terminal args" in res.lexical
    assert "ruby command line interface builder" in res.vector

    # Verify prompt formatting
    call_args = mock_llama.create_completion.call_args[1]
    assert "<|im_start|>user /no_think Expand this search query: cli tool<|im_end|>\n" in call_args["prompt"]
    assert call_args["stop"] == ["<|im_end|>"]


def test_query_expander_graceful_fallback():
    mock_manager = MagicMock()
    # 1. Disabled
    expander_disabled = QueryExpander(model_manager=mock_manager, enabled=False)
    res = expander_disabled.expand("search query")
    assert res.lexical == []
    assert res.vector == []
    assert mock_manager.get_expander_model.call_count == 0

    # 2. Model returns None
    mock_manager.get_expander_model.return_value = None
    expander = QueryExpander(model_manager=mock_manager, enabled=True)
    res = expander.expand("search query")
    assert res.lexical == []
    assert res.vector == []

    # 3. Model raises Exception
    mock_llama = MagicMock()
    mock_llama.create_completion.side_effect = RuntimeError("Inference out of memory")
    mock_manager.get_expander_model.return_value = mock_llama
    res = expander.expand("search query")
    assert res.lexical == []
    assert res.vector == []


# -------------------------------------------------------------------------
# Step 5: Cross-Encoder Reranker
# -------------------------------------------------------------------------

def test_reranker_prompt_and_scoring():
    mock_manager = MagicMock()
    mock_llama = MagicMock()
    mock_manager.get_reranker_model.return_value = mock_llama

    reranker = CrossEncoderReranker(model_manager=mock_manager, enabled=True)

    # 1. Successful scoring with logprob for 'yes'
    mock_llama.create_completion.return_value = {
        "choices": [
            {
                "text": "yes",
                "logprobs": {
                    "top_logprobs": [{"yes": -0.15, "no": -2.5}]
                },
            }
        ]
    }
    score = reranker.score_pair("cli framework", "Thor: powerful CLI tool")
    assert math.isclose(score, math.exp(-0.15), rel_tol=1e-3)

    # Verify prompt formatting
    prompt_used = mock_llama.create_completion.call_args[1]["prompt"]
    assert "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query.<|im_end|>\n" in prompt_used
    assert "<Query>: cli framework\n<Document>: Thor: powerful CLI tool<|im_end|>" in prompt_used

    # 2. Scoring with logprob for 'no' dominant
    mock_llama.create_completion.return_value = {
        "choices": [
            {
                "text": "no",
                "logprobs": {
                    "top_logprobs": [{"no": -0.05, "yes": -3.5}]
                },
            }
        ]
    }
    score_low = reranker.score_pair("cli framework", "Nokogiri: XML and HTML parser")
    assert math.isclose(score_low, math.exp(-3.5), rel_tol=1e-3)
    assert score > score_low


def test_reranker_rerank_items():
    mock_manager = MagicMock()
    reranker = CrossEncoderReranker(model_manager=mock_manager, enabled=True)

    # Mock score_pair directly to test sorting and top_k
    scores = {
        "gem_a": 0.2,
        "gem_b": 0.95,
        "gem_c": 0.65,
    }
    reranker.score_pair = lambda query, passage, model=None: scores.get(passage, 0.0)

    items = [
        {"name": "gem_a", "desc": "first"},
        {"name": "gem_b", "desc": "second"},
        {"name": "gem_c", "desc": "third"},
    ]

    reranked = reranker.rerank(
        query="test query",
        items=items,
        text_extractor=lambda it: it["name"],
        top_k=2,
    )

    assert len(reranked) == 2
    assert reranked[0]["name"] == "gem_b"
    assert reranked[0]["rerank_score"] == 0.95
    assert reranked[1]["name"] == "gem_c"
    assert reranked[1]["rerank_score"] == 0.65


def test_reranker_graceful_fallback():
    mock_manager = MagicMock()
    items = [{"name": "gem_1"}, {"name": "gem_2"}]

    # Disabled
    reranker_disabled = CrossEncoderReranker(model_manager=mock_manager, enabled=False)
    assert reranker_disabled.rerank("query", items) == items

    # Model is None
    mock_manager.get_reranker_model.return_value = None
    reranker = CrossEncoderReranker(model_manager=mock_manager, enabled=True)
    assert reranker.rerank("query", items) == items

    # Model raises Exception
    mock_llama = MagicMock()
    mock_llama.create_completion.side_effect = RuntimeError("GPU crash")
    mock_manager.get_reranker_model.return_value = mock_llama
    assert reranker.rerank("query", items) == items


# -------------------------------------------------------------------------
# Step 6: Hybrid Search Integration
# -------------------------------------------------------------------------

def test_hybrid_search_query_expansion_and_reranking(tmp_path):
    # Setup test sqlite db
    db_file = tmp_path / "test.sqlite"
    import sqlite3
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE inventory (name TEXT PRIMARY KEY, description TEXT, source_code_uri TEXT, context7_id TEXT, preferred INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE classified_gems (name TEXT PRIMARY KEY, classification TEXT)")
    conn.commit()
    conn.close()

    mock_expander = MagicMock()
    mock_expander.is_enabled = True
    mock_expander.expand.return_value = QueryExpansion(
        lexical=["cli toolkit"],
        vector=["command line args"],
        raw="mocked",
    )

    mock_reranker = MagicMock()
    mock_reranker.is_enabled = True

    # Vector search mock recording queries
    searched_vector_queries = []
    def mock_vector_search(q, cats, limit):
        searched_vector_queries.append(q)
        return [{"name": "thor", "description": "CLI toolkit", "category": "cli_libraries"}]

    # Keyword search mock recording queries
    searched_keyword_queries = []
    def mock_fts_search(q, cats, limit):
        searched_keyword_queries.append(q)
        return [{"name": "gli", "description": "Git-like interface CLI", "category": "cli_libraries"}]

    mock_reranker.rerank.side_effect = lambda query, items, text_extractor, top_k: [
        dict(it, rerank_score=0.99) for it in items[:top_k]
    ]

    results = hybrid_search(
        query="cli framework",
        limit=2,
        vector_search=mock_vector_search,
        fts_search=mock_fts_search,
        db_path=str(db_file),
        query_expander=mock_expander,
        reranker=mock_reranker,
    )

    # 1. Multi-pass queries executed
    assert "cli framework" in searched_vector_queries
    assert "command line args" in searched_vector_queries
    assert "cli framework" in searched_keyword_queries
    assert "cli toolkit" in searched_keyword_queries

    # 2. Reranker invoked
    assert mock_reranker.rerank.call_count == 1
    call_args = mock_reranker.rerank.call_args[1]
    assert call_args["query"] == "cli framework"
    assert call_args["top_k"] == 2
    assert len(results) == 2
    assert results[0]["rerank_score"] == 0.99


def test_hybrid_search_candidate_pool_sizing(tmp_path):
    db_file = tmp_path / "test.sqlite"
    import sqlite3
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE inventory (name TEXT PRIMARY KEY, description TEXT, source_code_uri TEXT, context7_id TEXT, preferred INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE classified_gems (name TEXT PRIMARY KEY, classification TEXT)")
    conn.commit()
    conn.close()

    mock_reranker = MagicMock()
    mock_reranker.is_enabled = True

    captured_limit = []
    def mock_fts(q, cats, limit):
        captured_limit.append(limit)
        return []

    # limit=2, multiplier=3 -> 6, clamped to minimum 15
    hybrid_search(
        query="test",
        limit=2,
        fts_search=mock_fts,
        db_path=str(db_file),
        reranker=mock_reranker,
    )
    assert captured_limit[0] == 15

    # limit=20, multiplier=3 -> 60, clamped to maximum 50
    captured_limit.clear()
    hybrid_search(
        query="test",
        limit=20,
        fts_search=mock_fts,
        db_path=str(db_file),
        reranker=mock_reranker,
    )
    assert captured_limit[0] == 50


# -------------------------------------------------------------------------
# Step 7: GraphRAG Retrieve Integration
# -------------------------------------------------------------------------

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
        ],
    }

def test_graphrag_retrieve_expansion_and_reranking(sample_graph_data):
    service = GraphRAGService()
    service.graph_data = sample_graph_data
    service._index_graph_memory()

    mock_embeddings = MagicMock()
    searched_queries = []
    def mock_emb_search(q, k):
        searched_queries.append(q)
        return [("class:Thor", 0.9)]

    mock_embeddings.search.side_effect = mock_emb_search
    service._embeddings = mock_embeddings

    mock_expander = MagicMock()
    mock_expander.is_enabled = True
    mock_expander.expand.return_value = QueryExpansion(
        lexical=["cli parser"],
        vector=["command line framework"],
    )

    mock_reranker = MagicMock()
    mock_reranker.is_enabled = True
    mock_reranker.rerank.side_effect = lambda query, items, text_extractor, top_k: [
        dict(it, rerank_score=0.98) for it in items[:top_k]
    ]

    res = service.retrieve(
        query="cli tool",
        top_k=1,
        query_expander=mock_expander,
        reranker=mock_reranker,
    )

    # Verify query expansion searched multiple vector terms
    assert "cli tool" in searched_queries
    assert "command line framework" in searched_queries

    # Verify reranker was called
    assert mock_reranker.rerank.call_count == 1
    assert len(res["primary_nodes"]) == 1
    assert res["primary_nodes"][0]["id"] == "class:Thor"
    assert res["primary_nodes"][0]["rerank_score"] == 0.98

    # Verify 1-hop graph neighborhood expansion was performed
    assert len(res["expanded_nodes"]) > 0 or len(res["edges"]) > 0


def test_graphrag_retrieve_fallback(sample_graph_data):
    service = GraphRAGService()
    service.graph_data = sample_graph_data
    service._index_graph_memory()

    # Expander and reranker fail/raise
    mock_expander = MagicMock()
    mock_expander.is_enabled = True
    mock_expander.expand.side_effect = Exception("Expander error")

    mock_reranker = MagicMock()
    mock_reranker.is_enabled = True
    mock_reranker.rerank.side_effect = Exception("Reranker error")

    # Should not raise; falls back gracefully
    res = service.retrieve(
        query="Thor",
        top_k=2,
        query_expander=mock_expander,
        reranker=mock_reranker,
    )

    assert len(res["primary_nodes"]) > 0
    assert any(n["name"].lower() == "thor" or "thor" in n["id"].lower() for n in res["primary_nodes"])
