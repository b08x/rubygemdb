"""Comprehensive automated tests for Recursive Parent-Child Chunking.

Covers:
1. ChunkConfig defaults and validation rules.
2. Text preprocessing (whitespace normalization, URL/email stripping with code-block bypass).
3. 'paragraph' vs 'full_doc' parent modes.
4. Recursive delimiter cascade and terminal character fallback for unbroken tokens.
5. Child chunk character overlap and bounded lengths.
6. Markdown fenced code block atomic preservation and overflow handling.
7. Hierarchical preview_chunks inspection hook with exact character offsets (parent_text[start:end] == child_text).
8. TxtaiAgent child chunk indexing and search deduplication by parent gem.
9. GraphRAGService long docstring chunking, child-to-parent node resolution, and 1-hop expansion.
10. Settings chunking defaults in core/config.py.
"""

from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError

from rubygemdb.core.config import settings
from rubygemdb.services.chunking import (
    ChunkConfig,
    ChildChunk,
    ParentChunk,
    ChunkPreviewResult,
    RecursiveParentChildChunker,
    preprocess_text,
    preview_chunks,
    split_text_recursive,
)
from rubygemdb.agent import create_child_chunks, TxtaiAgent
from rubygemdb.services.graphrag import GraphRAGService


# ---------------------------------------------------------------------------
# 1. ChunkConfig defaults and validation
# ---------------------------------------------------------------------------

def test_chunk_config_defaults():
    cfg = ChunkConfig()
    assert cfg.parent_mode == "paragraph"
    assert cfg.parent_max_chars == 4000
    assert cfg.child_max_chars == 512
    assert cfg.child_overlap_chars == 50
    assert cfg.delimiters == ["\n\n", "\n", ". ", "? ", "! ", " ", ""]
    assert cfg.normalize_whitespace is True
    assert cfg.strip_urls_emails is False


def test_chunk_config_validation():
    # child_max_chars must be > 0
    with pytest.raises(ValidationError):
        ChunkConfig(child_max_chars=0)

    # parent_max_chars must be >= child_max_chars
    with pytest.raises(ValidationError):
        ChunkConfig(parent_max_chars=100, child_max_chars=200)

    # child_overlap_chars cannot be negative
    with pytest.raises(ValidationError):
        ChunkConfig(child_overlap_chars=-1)

    # child_overlap_chars must be < child_max_chars
    with pytest.raises(ValidationError):
        ChunkConfig(child_max_chars=100, child_overlap_chars=100)

    # parent_mode must be paragraph or full_doc
    with pytest.raises(ValidationError):
        ChunkConfig(parent_mode="invalid_mode")  # type: ignore


def test_settings_chunking_defaults():
    assert settings.chunk_parent_mode == "paragraph"
    assert settings.chunk_parent_max_chars == 4000
    assert settings.chunk_child_max_chars == 512
    assert settings.chunk_child_overlap_chars == 50
    assert settings.chunk_normalize_whitespace is True
    assert settings.chunk_strip_urls_emails is False


# ---------------------------------------------------------------------------
# 2. Text preprocessing and code block safeguards
# ---------------------------------------------------------------------------

def test_preprocess_whitespace_normalization():
    cfg = ChunkConfig(normalize_whitespace=True, strip_urls_emails=False)
    raw = "Line 1   with    extra  spaces\t\tand tabs.   \n\n\n\nLine 2 with triple newlines.\n\n"
    processed = preprocess_text(raw, cfg)
    assert "Line 1 with extra spaces and tabs." in processed
    assert "\n\n\n" not in processed
    assert processed == "Line 1 with extra spaces and tabs.\n\nLine 2 with triple newlines."


def test_preprocess_url_email_stripping_with_code_block_bypass():
    cfg = ChunkConfig(normalize_whitespace=True, strip_urls_emails=True)
    raw = (
        "Check out https://rubygems.org and send bugs to dev@example.com.\n\n"
        "```ruby\n"
        "# Valid code must keep its URLs and emails intact!\n"
        "client = API::Client.new('https://api.github.com')\n"
        "admin_email = 'root@system.org'\n"
        "```\n\n"
        "Documentation at http://docs.example.org or contact info@example.org."
    )
    processed = preprocess_text(raw, cfg)
    
    # Outside code block: URLs and emails must be stripped
    assert "https://rubygems.org" not in processed
    assert "dev@example.com" not in processed
    assert "http://docs.example.org" not in processed
    assert "info@example.org" not in processed

    # Inside code block: URLs and emails must be preserved
    assert "client = API::Client.new('https://api.github.com')" in processed
    assert "admin_email = 'root@system.org'" in processed


def test_preprocess_empty_and_disabled():
    assert preprocess_text("") == ""
    assert preprocess_text("   ") == ""
    
    cfg = ChunkConfig(normalize_whitespace=False, strip_urls_emails=False)
    raw = "  Hello   world\n\n\nhttps://example.com  "
    assert preprocess_text(raw, cfg) == raw


# ---------------------------------------------------------------------------
# 3. 'paragraph' vs 'full_doc' parent modes
# ---------------------------------------------------------------------------

def test_parent_mode_paragraph_partition():
    # Long text with 4 distinct paragraphs
    paras = [
        "Paragraph 1: " + ("alpha " * 50),
        "Paragraph 2: " + ("beta " * 50),
        "Paragraph 3: " + ("gamma " * 50),
        "Paragraph 4: " + ("delta " * 50),
    ]
    doc = "\n\n".join(paras)
    
    # Configure parent_max_chars small enough to force multiple parent chunks
    cfg = ChunkConfig(
        parent_mode="paragraph",
        parent_max_chars=600,
        child_max_chars=150,
        child_overlap_chars=20,
    )
    chunker = RecursiveParentChildChunker(config=cfg)
    parents = chunker.chunk_document(doc, doc_id="my_gem")

    assert len(parents) > 1
    for p in parents:
        assert p.parent_id.startswith("my_gem_p")
        assert len(p.text) <= 600
        assert len(p.children) > 0


def test_parent_mode_full_doc():
    paras = [
        "Paragraph 1: " + ("alpha " * 50),
        "Paragraph 2: " + ("beta " * 50),
    ]
    doc = "\n\n".join(paras)
    
    cfg = ChunkConfig(
        parent_mode="full_doc",
        parent_max_chars=4000,
        child_max_chars=150,
        child_overlap_chars=20,
    )
    chunker = RecursiveParentChildChunker(config=cfg)
    parents = chunker.chunk_document(doc, doc_id="my_gem")

    # In full_doc mode, only 1 parent chunk is generated
    assert len(parents) == 1
    assert parents[0].parent_id == "my_gem_p0"
    assert len(parents[0].children) > 1


# ---------------------------------------------------------------------------
# 4. Recursive delimiter cascade and terminal character splitting
# ---------------------------------------------------------------------------

def test_delimiter_cascade_sentences_and_words():
    text = (
        "First sentence is here. Second sentence follows right after. "
        "Third sentence is also present! Fourth sentence has a question mark? "
        "Fifth sentence concludes the test."
    )
    cfg = ChunkConfig(child_max_chars=60, child_overlap_chars=15)
    children = split_text_recursive(text, config=cfg, parent_id="test_p0")

    assert len(children) > 1
    for child in children:
        assert len(child.text) <= 60
        # Character offsets must match parent slice exactly
        assert text[child.start_offset : child.end_offset] == child.text


def test_terminal_character_splitting_unbroken_string():
    # 1000 characters with no delimiters whatsoever
    unbroken = "z" * 1000
    cfg = ChunkConfig(child_max_chars=200, child_overlap_chars=30)
    children = split_text_recursive(unbroken, config=cfg, parent_id="unbroken_p0")

    assert len(children) == 6
    for child in children:
        assert len(child.text) <= 200
        assert unbroken[child.start_offset : child.end_offset] == child.text

    # Overlap between consecutive chunks must be bounded
    for i in range(len(children) - 1):
        c1 = children[i]
        c2 = children[i + 1]
        assert c2.start_offset > c1.start_offset  # strictly increasing
        overlap = c1.end_offset - c2.start_offset
        assert overlap <= 30


# ---------------------------------------------------------------------------
# 5. Overlap continuity and edge cases
# ---------------------------------------------------------------------------

def test_zero_overlap():
    text = "Short sentence one. Short sentence two. Short sentence three. Short sentence four."
    cfg = ChunkConfig(child_max_chars=40, child_overlap_chars=0)
    children = split_text_recursive(text, config=cfg, parent_id="p0")

    assert len(children) > 1
    for i in range(len(children) - 1):
        assert children[i + 1].start_offset >= children[i].end_offset


def test_no_infinite_loop_on_tiny_chunks():
    text = "The quick brown fox jumps over the lazy dog."
    cfg = ChunkConfig(child_max_chars=10, child_overlap_chars=3)
    children = split_text_recursive(text, config=cfg, parent_id="p0")

    assert len(children) > 1
    for child in children:
        assert len(child.text) <= 10
        assert text[child.start_offset : child.end_offset] == child.text


# ---------------------------------------------------------------------------
# 6. Markdown fenced code block atomic preservation
# ---------------------------------------------------------------------------

def test_fenced_code_block_preserved_atomically():
    code_block = "```ruby\ndef solve(x)\n  x * 2\nend\n```"
    text = f"Intro paragraph introducing the algorithm.\n\n{code_block}\n\nOutro paragraph explaining results."
    
    # child_max_chars is 80 (large enough to fit the code block of ~35 chars, but not the whole doc)
    cfg = ChunkConfig(child_max_chars=80, child_overlap_chars=10)
    children = split_text_recursive(text, config=cfg, parent_id="p0")

    # Find chunk containing the code block
    code_chunks = [c for c in children if "def solve(x)" in c.text]
    assert len(code_chunks) >= 1
    # The code block must be complete within its chunk, not split in the middle
    assert code_block in code_chunks[0].text


def test_fenced_code_block_overflow_splits_internally():
    # Long code block exceeding child_max_chars
    lines = [f"  x_{i} = compute_value({i})" for i in range(20)]
    long_code = "```ruby\ndef massive\n" + "\n".join(lines) + "\nend\n```"
    assert len(long_code) > 200

    cfg = ChunkConfig(child_max_chars=100, child_overlap_chars=20)
    children = split_text_recursive(long_code, config=cfg, parent_id="p0")

    assert len(children) > 1
    for child in children:
        assert len(child.text) <= 100
        assert long_code[child.start_offset : child.end_offset] == child.text


# ---------------------------------------------------------------------------
# 7. Hierarchical preview_chunks hook and exact offset invariant
# ---------------------------------------------------------------------------

def test_preview_chunks_hook_offsets_and_stats():
    text = (
        "RubyGemDB provides architectural classification for Ruby gems.\n\n"
        "It supports both heuristic classification and LLM fallback via Mistral AI.\n\n"
        "Gems are mapped to 21 architectural categories and 4 stack layers."
    )
    cfg = ChunkConfig(
        parent_mode="paragraph",
        parent_max_chars=200,
        child_max_chars=80,
        child_overlap_chars=15,
    )
    preview = preview_chunks(text, config=cfg, doc_id="preview_test")

    assert isinstance(preview, ChunkPreviewResult)
    assert preview.total_parents == len(preview.parent_chunks)
    assert preview.total_children > 0
    assert preview.avg_child_chars > 0.0
    assert preview.max_child_chars <= 80
    assert preview.min_child_chars > 0

    # Invariant: parent_text[child.start_offset : child.end_offset] == child.text
    for parent in preview.parent_chunks:
        assert isinstance(parent, ParentChunk)
        for child in parent.children:
            assert isinstance(child, ChildChunk)
            assert parent.text[child.start_offset : child.end_offset] == child.text


def test_preview_chunks_empty_text():
    preview = preview_chunks("", config=ChunkConfig())
    assert preview.total_parents == 0
    assert preview.total_children == 0
    assert preview.avg_child_chars == 0.0
    assert preview.max_child_chars == 0
    assert preview.min_child_chars == 0
    assert preview.parent_chunks == []


# ---------------------------------------------------------------------------
# 8. TxtaiAgent integration and legacy helper
# ---------------------------------------------------------------------------

def test_legacy_create_child_chunks_helper():
    text = "Short single paragraph."
    chunks = create_child_chunks(text, max_chars=512)
    assert chunks == [text]

    long_text = "Sentence 1. " * 30
    chunks = create_child_chunks(long_text, max_chars=100)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 100


def test_txtai_agent_vector_search_deduplication():
    # Mock txtai embeddings search returning multiple child chunks for the same gem
    agent = TxtaiAgent.__new__(TxtaiAgent)
    mock_embeddings = MagicMock()
    
    # 4 search hits from txtai representing child chunks of 2 gems:
    # 3 hits for 'nokogiri' (with different scores and child chunks), 1 hit for 'pdf-reader'
    mock_hits = [
        {
            "id": "nokogiri_p0_c1",
            "name": "nokogiri",
            "parent_text": "Nokogiri is an HTML, XML, SAX, and Reader parser.",
            "category": "document_parsing",
            "layer": "plumbing",
            "source_code_uri": "https://github.com/sparklemotion/nokogiri",
            "context7_id": "/sparklemotion/nokogiri",
            "score": 0.95,
            "type": "gem",
        },
        {
            "id": "nokogiri_p0_c0",
            "name": "nokogiri",
            "parent_text": "Nokogiri is an HTML, XML, SAX, and Reader parser.",
            "category": "document_parsing",
            "layer": "plumbing",
            "source_code_uri": "https://github.com/sparklemotion/nokogiri",
            "context7_id": "/sparklemotion/nokogiri",
            "score": 0.88,
            "type": "gem",
        },
        {
            "id": "pdf-reader_p0_c0",
            "name": "pdf-reader",
            "parent_text": "PDF reader extracts text and format from PDF files.",
            "category": "document_parsing",
            "layer": "plumbing",
            "source_code_uri": "https://github.com/yob/pdf-reader",
            "context7_id": "/yob/pdf-reader",
            "score": 0.82,
            "type": "gem",
        },
        {
            "id": "nokogiri_p0_c2",
            "name": "nokogiri",
            "parent_text": "Nokogiri is an HTML, XML, SAX, and Reader parser.",
            "category": "document_parsing",
            "layer": "plumbing",
            "source_code_uri": "https://github.com/sparklemotion/nokogiri",
            "context7_id": "/sparklemotion/nokogiri",
            "score": 0.70,
            "type": "gem",
        },
    ]
    mock_embeddings.search.return_value = mock_hits
    agent.embeddings = mock_embeddings

    # Request limit=5
    results = agent._vector_search("parser", categories=None, limit=5)

    # Must be deduplicated by gem name! Nokogiri appears only once with highest score (0.95)
    names = [r["name"] for r in results]
    assert names == ["nokogiri", "pdf-reader"]
    assert results[0]["score"] == 0.95
    assert results[0]["description"] == "Nokogiri is an HTML, XML, SAX, and Reader parser."
    assert results[1]["score"] == 0.82


# ---------------------------------------------------------------------------
# 9. GraphRAGService child chunking and parent node resolution
# ---------------------------------------------------------------------------

def test_graphrag_build_vector_index_chunks_long_docstrings(tmp_path):
    long_doc = "Detailed method explanation: " + ("token " * 120)
    assert len(long_doc) > 600

    graph_data = {
        "version": "1.0",
        "gems": ["thor"],
        "nodes": [
            {
                "id": "method:Thor#start",
                "type": "method",
                "name": "start",
                "gem": "thor",
                "data": {
                    "qualified_name": "Thor.start",
                    "signature": "def start(args)",
                    "docstring": long_doc,
                },
            }
        ],
        "edges": [],
    }

    mock_embeddings = MagicMock()
    indexed_docs = []
    mock_embeddings.index.side_effect = lambda stream: indexed_docs.extend(list(stream))

    service = GraphRAGService(base_dir=tmp_path, embeddings=mock_embeddings)
    service.graph_data = graph_data
    service._index_graph_memory()

    service._build_vector_index(tmp_path / "txtai")

    # The long method docstring should have produced multiple child chunk documents
    assert len(indexed_docs) > 1
    for doc in indexed_docs:
        assert doc["node_id"] == "method:Thor#start"
        assert doc["parent_node_id"] == "method:Thor#start"
        assert doc["id"].startswith("method:Thor#start_")


def test_graphrag_retrieve_resolves_child_chunk_to_parent_node(tmp_path):
    graph_data = {
        "version": "1.0",
        "gems": ["thor"],
        "nodes": [
            {
                "id": "class:Thor",
                "type": "class",
                "name": "Thor",
                "gem": "thor",
                "data": {"qualified_name": "Thor"},
            },
            {
                "id": "method:Thor#start",
                "type": "method",
                "name": "start",
                "gem": "thor",
                "data": {"qualified_name": "Thor.start"},
            },
        ],
        "edges": [
            {
                "source": "class:Thor",
                "target": "method:Thor#start",
                "type": "defines",
            }
        ],
    }

    mock_embeddings = MagicMock()
    # Vector hit returns child chunk ID referencing method:Thor#start
    mock_embeddings.search.return_value = [
        {"id": "method:Thor#start_p0_c0", "parent_node_id": "method:Thor#start", "score": 0.9}
    ]

    service = GraphRAGService(base_dir=tmp_path, embeddings=mock_embeddings)
    service.graph_data = graph_data
    service._index_graph_memory()

    retrieval = service.retrieve("start CLI", top_k=1)

    primary_ids = [n["id"] for n in retrieval["primary_nodes"]]
    # Child hit was successfully resolved back to parent AST node id!
    assert primary_ids == ["method:Thor#start"]

    # 1-hop expansion includes class:Thor
    expanded_ids = [n["id"] for n in retrieval["expanded_nodes"]]
    assert "class:Thor" in expanded_ids
