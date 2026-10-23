"""Hybrid search tests: RRF fusion, phase filtering, FTS5 index build."""
import sqlite3

import pytest

from rubygemdb.models.phases import categories_for
from rubygemdb.services.hybrid_search import (
    build_fts_index,
    ensure_fts_index,
    fts_match_expression,
    hybrid_search,
    make_fts_search,
    narrow_categories,
)

GEMS = [
    # name, description, category
    ("nokogiri", "HTML and XML parser with CSS selectors", "document_parsing"),
    ("pdf-reader", "Extract text from PDF documents", "document_parsing"),
    ("thor", "Build powerful command-line interfaces", "cli_libraries"),
    ("prawn", "Generate PDF documents", "document_generation"),
    ("rspec", "Behaviour-driven testing framework", "testing_qa"),
]


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE inventory (name TEXT PRIMARY KEY, description TEXT, source_code_uri TEXT, context7_id TEXT, preferred INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE classified_gems (name TEXT PRIMARY KEY, classification TEXT)")
    for name, desc, category in GEMS:
        conn.execute("INSERT INTO inventory (name, description) VALUES (?, ?)", (name, desc))
        conn.execute(
            "INSERT INTO classified_gems (name, classification) VALUES (?, ?)",
            (name, f'{{"primary": "{category}"}}'),
        )
    conn.commit()
    conn.close()
    return path


def _vector(gems_by_query):
    def search(query, categories, limit):
        return [
            {"name": n, "description": d, "category": c}
            for n, d, c in gems_by_query.get(query, [])
            if categories is None or c in categories
        ][:limit]
    return search


def test_gem_fts_exists_after_build(db_path):
    count = build_fts_index(db_path)
    assert count == len(GEMS)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM gem_fts").fetchone()
        assert rows[0] == len(GEMS)
    finally:
        conn.close()


def test_ensure_rebuilds_stale_index(db_path):
    ensure_fts_index(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM gem_fts")
    conn.commit()
    conn.close()
    ensure_fts_index(db_path)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM gem_fts").fetchone()[0] == len(GEMS)
    finally:
        conn.close()


def test_keyword_only_gem_still_appears(db_path):
    build_fts_index(db_path)
    results = hybrid_search(
        "PDF",
        limit=5,
        vector_search=_vector({}),  # vector search returns nothing
        fts_search=make_fts_search(db_path),
    )
    assert [r["name"] for r in results], "expected FTS-only results"


def test_vector_only_gem_still_appears(db_path):
    build_fts_index(db_path)
    results = hybrid_search(
        "pdf text extraction with context",
        limit=5,
        vector_search=_vector({"pdf text extraction with context": [("pdf-reader", "Extract text from PDF documents", "document_parsing")]}),
        fts_search=make_fts_search(db_path),
    )
    names = [r["name"] for r in results]
    assert "pdf-reader" in names


def test_gem_found_by_both_outranks_single_source(db_path):
    build_fts_index(db_path)
    query = "pdf"
    vector = _vector({query: [("pdf-reader", "Extract text from PDF documents", "document_parsing")]})
    results = hybrid_search(query, limit=5, vector_search=vector, fts_search=make_fts_search(db_path))
    assert results, "no results"
    assert results[0]["name"] == "pdf-reader", "dual-source gem must rank first"


def test_phase_filter_excludes_other_phases(db_path):
    build_fts_index(db_path)
    results = hybrid_search(
        "parser",
        phase="input",
        limit=10,
        vector_search=_vector({"parser": GEMS}),  # returns gems from every phase
        fts_search=make_fts_search(db_path),
    )
    assert results
    for hit in results:
        assert hit["phase"] == "input", f"{hit['name']} leaked into input phase"
        assert hit["category"] in categories_for("input")


def test_fts_special_characters_do_not_raise(db_path):
    build_fts_index(db_path)
    assert fts_match_expression('a"b (c) -d AND') == '"a" OR "b" OR "c" OR "d" OR "and"'
    results = hybrid_search(
        'pdf (parser) "quotes" AND NOT',
        limit=5,
        fts_search=make_fts_search(db_path),
    )
    assert isinstance(results, list)


def test_results_carry_category_layer_phase(db_path):
    build_fts_index(db_path)
    results = hybrid_search("nokogiri", limit=3, fts_search=make_fts_search(db_path))
    assert results
    top = results[0]
    assert top["name"] == "nokogiri"
    assert top["category"] == "document_parsing"
    assert top["layer"] == "plumbing"
    assert top["phase"] == "input"
    assert "score" in top


def test_fts_match_expression_require_all_drops_stopwords():
    assert (
        fts_match_expression("chunks of text with context", require_all=True)
        == '"chunks" AND "text" AND "context"'
    )


def test_strict_and_match_beats_single_token_coincidence(db_path):
    """A gem matching only one query token must not outrank full matches."""
    build_fts_index(db_path)
    # prawn matches "pdf" alone; only pdf-reader contains both tokens.
    results = hybrid_search(
        "extract pdf",
        limit=5,
        vector_search=_vector({}),
        fts_search=make_fts_search(db_path),
    )
    assert [r["name"] for r in results] == ["pdf-reader"]


def test_loose_or_fallback_when_strict_match_is_empty(db_path):
    """Tokens that never co-occur still find gems via the OR fallback."""
    build_fts_index(db_path)
    results = hybrid_search(
        "parser testing",
        limit=10,
        vector_search=_vector({}),
        fts_search=make_fts_search(db_path),
    )
    names = [r["name"] for r in results]
    assert "nokogiri" in names  # matches "parser"
    assert "rspec" in names  # matches "testing"


def test_narrow_categories_targets_query_categories():
    processing = categories_for("processing")
    assert narrow_categories("chunk text with context", processing) == ["text_search"]
    assert narrow_categories("background job queue workers", processing) == ["background_jobs"]
    assert narrow_categories("resize images and thumbnails", processing) == ["media_processing"]
    # Unmatched queries keep the full phase bucket.
    assert narrow_categories("abstract symbolic manipulation", processing) == processing


def test_phase_search_narrows_to_query_targeted_category(db_path):
    """A phase filter plus a targeted query restricts results to one category."""
    build_fts_index(db_path)
    results = hybrid_search(
        "parse html",
        phase="input",
        limit=10,
        vector_search=_vector({"parse html": GEMS}),
        fts_search=make_fts_search(db_path),
    )
    assert results
    assert all(hit["category"] == "document_parsing" for hit in results)
