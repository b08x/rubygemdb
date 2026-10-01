"""Preferred-gem flag tests: storage round-trip, search rank boost,
stack-manifest injection."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rubygemdb.models.gem import GemClassification, GemEntry
from rubygemdb.services.hybrid_search import PREFERRED_BOOST, hybrid_search
from rubygemdb.services.stack import StackService
from rubygemdb.storage.sqlite_storage import SQLiteStorage

GEMS = [
    ("nokogiri", "HTML and XML parser with CSS selectors", "document_parsing"),
    ("oga", "Another HTML and XML parser", "document_parsing"),
]


def _db(path: str) -> str:
    """Build a minimal database with both gems classified and indexed."""
    storage = SQLiteStorage.__new__(SQLiteStorage)
    storage.db_path = path
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE inventory (name TEXT PRIMARY KEY, version TEXT, category TEXT, "
        "description TEXT, homepage TEXT, source_code_uri TEXT, context7_id TEXT, "
        "verified INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE classified_gems (name TEXT PRIMARY KEY, classification TEXT, "
        "role TEXT, capabilities TEXT, risks TEXT, signals TEXT, dependencies TEXT, description TEXT)"
    )
    conn.commit()
    conn.close()
    storage._init_db()  # adds the preferred column via migration

    for name, desc, category in GEMS:
        entry = GemEntry(
            name=name,
            classification=GemClassification(primary=category),
            description=desc,
        )
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO inventory (name, description) VALUES (?, ?)", (name, desc)
            )
        storage.save_classified_gems([entry])
    return path


def test_preferred_column_migration(tmp_path):
    path = _db(str(tmp_path / "test.sqlite"))
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(inventory)")}
    conn.close()
    assert "preferred" in columns


def test_set_and_get_preferred(tmp_path):
    path = _db(str(tmp_path / "test.sqlite"))
    storage = SQLiteStorage.__new__(SQLiteStorage)
    storage.db_path = path

    storage.set_preferred(["nokogiri"], True)
    preferred = storage.get_preferred()
    assert [g["name"] for g in preferred] == ["nokogiri"]
    assert preferred[0]["layer"] == "plumbing"

    storage.set_preferred(["nokogiri"], False)
    assert storage.get_preferred() == []


def test_load_classified_gems_carries_preferred(tmp_path):
    path = _db(str(tmp_path / "test.sqlite"))
    storage = SQLiteStorage.__new__(SQLiteStorage)
    storage.db_path = path

    storage.set_preferred(["oga"], True)
    gems = storage.load_classified_gems()
    by_name = {g.name: g for g in gems}
    assert by_name["oga"].preferred is True
    assert by_name["nokogiri"].preferred is False


def test_preferred_gem_gets_search_boost(tmp_path):
    path = _db(str(tmp_path / "test.sqlite"))
    storage = SQLiteStorage.__new__(SQLiteStorage)
    storage.db_path = path

    from rubygemdb.services.hybrid_search import build_fts_index

    build_fts_index(path)

    def vector_search(query, categories, limit):
        # Both gems match equally in the vector leg.
        return [
            {"name": "nokogiri", "description": "HTML and XML parser", "category": "document_parsing"},
            {"name": "oga", "description": "Another HTML and XML parser", "category": "document_parsing"},
        ]

    base = hybrid_search("html parser", vector_search=vector_search, db_path=path)
    base_scores = {h["name"]: h["score"] for h in base}
    assert all(h["preferred"] is False for h in base)

    storage.set_preferred(["oga"], True)
    boosted = hybrid_search("html parser", vector_search=vector_search, db_path=path)
    boosted_scores = {h["name"]: h["score"] for h in boosted}
    assert boosted_scores["oga"] == round(base_scores["oga"] + PREFERRED_BOOST, 6)
    assert boosted_scores["nokogiri"] == base_scores["nokogiri"]
    by_name = {h["name"]: h for h in boosted}
    assert by_name["oga"]["preferred"] is True
    assert by_name["nokogiri"]["preferred"] is False


def test_stack_manifest_includes_preferred(tmp_path):
    class _NoIndex:
        """Stub txtai index: no context matches, exercises the injection path."""

        def search(self, query, limit):
            return []

    path = _db(str(tmp_path / "test.sqlite"))
    storage = SQLiteStorage.__new__(SQLiteStorage)
    storage.db_path = path
    storage.set_preferred(["oga"], True)

    service = StackService(index=_NoIndex(), storage=storage)
    manifest = service.build_stack("html parsing tool")

    preferred_entries = [
        e
        for layer in manifest["layers"]
        for e in layer["entries"]
        if e["source"] == "preferred"
    ]
    assert [e["gem"] for e in preferred_entries] == ["oga"]
    assert preferred_entries[0]["role"] == "user preferred pick"
    assert preferred_entries[0]["layer"] == "plumbing"
