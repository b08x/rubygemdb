"""Hybrid gem search: txtai vector search + SQLite FTS5 keyword search,
merged with reciprocal rank fusion.

A gem that ranks in only one source still appears in the fused results;
a gem ranked by both outranks single-source gems. Results carry category,
layer, and build phase, and phase filtering restricts results to that
phase's categories.
"""

import json
import re
import sqlite3
from typing import Callable, Optional

from rubygemdb.core.config import settings
from rubygemdb.models.categories import CATEGORY_BY_SLUG
from rubygemdb.models.phases import PHASE_BY_CATEGORY, categories_for, phase_for
from rubygemdb.services.stack import guess_category

RRF_K = 60

# Rank bonus applied to gems the user marked preferred. A single-source RRF
# hit scores ~1/61 = 0.0164, so 0.005 lifts a preferred gem above its
# same-class peers without outranking genuine two-source matches.
PREFERRED_BOOST = 0.005

# Generic words that carry no gem-relevance; filtered out of the strict
# (AND-joined) FTS match so precision does not drown in filler tokens.
FTS_STOPWORDS = frozenset(
    """
    a an the and or with for of to in on at by from into as is are be was were
    that this these those it its using use used uses build building create
    creating make making application app project tool toolkit library
    libraries gem gems ruby support supports provide provides simple simply
    easily more most very robust things thing want need needs like
    """.split()
)

# Callable contract for injectable search backends:
#   search(query: str, categories: list[str] | None, limit: int) -> list[dict]
# Each dict may carry name, description, category, layer, source_code_uri,
# context7_id, and a source-specific score.
SearchCallable = Callable[[str, Optional[list[str]], int], list[dict]]


def _resolve_category(name: str, classification: str | None) -> str | None:
    category: str | None = None
    if classification:
        try:
            category = (json.loads(classification) or {}).get("primary")
        except (ValueError, TypeError):
            category = None
    if category not in PHASE_BY_CATEGORY:
        category = guess_category(name)
    return category


def build_fts_index(db_path: str | None = None) -> int:
    """(Re)build the gem_fts FTS5 table from inventory joined with classified_gems.

    Returns the number of indexed gems.
    """
    db_path = db_path or str(settings.sqlite_db_file)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS gem_fts "
            "USING fts5(name, description, category, tokenize='unicode61')"
        )
        rows = conn.execute(
            """
            SELECT i.name, i.description, c.classification
            FROM inventory i
            LEFT JOIN classified_gems c ON i.name = c.name
            """
        ).fetchall()
        conn.execute("DELETE FROM gem_fts")
        resolved = [
            (name, desc or f"Ruby gem {name}", _resolve_category(name, cls))
            for name, desc, cls in rows
        ]
        conn.executemany(
            "INSERT INTO gem_fts(name, description, category) VALUES (?, ?, ?)",
            resolved,
        )
        conn.commit()
        return len(resolved)
    finally:
        conn.close()


def ensure_fts_index(db_path: str | None = None) -> None:
    """Build gem_fts if it is missing or stale (row count differs from inventory)."""
    db_path = db_path or str(settings.sqlite_db_file)
    conn = sqlite3.connect(db_path)
    try:
        try:
            inventory_count: Optional[int] = conn.execute(
                "SELECT COUNT(*) FROM inventory"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            return
        try:
            fts_count: Optional[int] = conn.execute("SELECT COUNT(*) FROM gem_fts").fetchone()[0]
        except sqlite3.OperationalError:
            fts_count = None
    finally:
        conn.close()
    if inventory_count is None or fts_count == inventory_count:
        return
    build_fts_index(db_path)


def fts_match_expression(query: str, require_all: bool = False) -> str:
    """Turn a free-text query into a safe FTS5 MATCH expression.

    Tokens are extracted and quoted individually so FTS syntax characters in
    the user's query can never raise. By default tokens are OR-joined (any
    token may match); with require_all=True stopwords are dropped and the
    remaining tokens are AND-joined (every token must match), which trades
    recall for precision.
    """
    tokens = re.findall(r"\w+(?:-\w+)*", query.lower())
    if require_all:
        tokens = [t for t in tokens if t not in FTS_STOPWORDS]
        if not tokens:
            return ""
        return " AND ".join(f'"{token}"' for token in tokens)
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


def _keyword_matches(token: str, keyword: str) -> bool:
    """Stem-tolerant token/keyword match: equality, plural, or shared prefix/suffix.

    Prefix/suffix matching (minimum 4 characters) lets "context" hit the
    "text" keyword and "segment" hit "segmenter", while mid-word substrings
    ("form" inside "transformation") do not count.
    """
    if token == keyword:
        return True
    if token == keyword + "s" or keyword == token + "s":
        return True
    if len(keyword) >= 4 and (token.startswith(keyword) or token.endswith(keyword)):
        return True
    if len(token) >= 4 and (keyword.startswith(token) or keyword.endswith(token)):
        return True
    if len(token) >= 3 and keyword.startswith(token):
        return True
    return False


def query_targets(query: str, categories: list[str]) -> list[str]:
    """The categories whose keywords the query stem-matches, without fallback."""
    tokens = [t for t in re.findall(r"\w+(?:-\w+)*", query.lower()) if t not in FTS_STOPWORDS]
    if not tokens:
        return []
    matched: list[str] = []
    for slug in categories:
        cat = CATEGORY_BY_SLUG.get(slug)
        if cat is None:
            continue
        if any(
            _keyword_matches(token, keyword)
            for keyword in (k.lower() for k in cat.keywords)
            for token in tokens
        ):
            matched.append(slug)
    return matched


def narrow_categories(query: str, categories: list[str]) -> list[str]:
    """Restrict a category filter to the categories the query actually targets.

    When no category matches, the full list is returned unchanged so an
    unmatched query never loses candidates.
    """
    return query_targets(query, categories) or categories


def make_fts_search(db_path: str | None = None) -> SearchCallable:
    """Build an FTS5 keyword search callable bound to a SQLite database.

    Tries the strict AND-joined match first; when that returns nothing it
    falls back to the loose OR-joined match, so a precise query never drags
    in single-token coincidences.
    """
    resolved_db = db_path or str(settings.sqlite_db_file)

    def run_query(expression: str, categories: Optional[list[str]], limit: int) -> list[dict]:
        sql = (
            "SELECT gem_fts.name, gem_fts.description, gem_fts.category, "
            "i.source_code_uri, i.context7_id, i.preferred "
            "FROM gem_fts JOIN inventory AS i ON i.name = gem_fts.name "
            "WHERE gem_fts MATCH ?"
        )
        params: list = [expression]
        if categories:
            placeholders = ", ".join("?" for _ in categories)
            sql += f" AND gem_fts.category IN ({placeholders})"
            params.extend(categories)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            conn = sqlite3.connect(resolved_db, uri=True)
        except sqlite3.Error:
            return []
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
        return [
            {
                "name": row["name"],
                "description": row["description"],
                "category": row["category"],
                "source_code_uri": row["source_code_uri"],
                "context7_id": row["context7_id"],
                "preferred": bool(row["preferred"]),
            }
            for row in rows
        ]

    def fts_search(query: str, categories: Optional[list[str]], limit: int) -> list[dict]:
        attempts: list[str] = []
        strict = fts_match_expression(query, require_all=True)
        loose = fts_match_expression(query)
        if strict:
            attempts.append(strict)
        if loose and loose != strict:
            attempts.append(loose)
        for expression in attempts:
            rows = run_query(expression, categories, limit)
            if rows:
                return rows
        return []

    return fts_search


def _annotate(hit: dict) -> dict:
    """Attach layer and phase from the hit's category; drop source-specific scores."""
    category = hit.get("category")
    cat = CATEGORY_BY_SLUG.get(category) if category else None
    annotated = {k: v for k, v in hit.items() if k != "score"}
    annotated["layer"] = cat.layer if cat else None
    annotated["phase"] = phase_for(category) if category else None
    return annotated


def hybrid_search(
    query: str,
    phase: Optional[str] = None,
    limit: int = 5,
    vector_search: Optional[SearchCallable] = None,
    fts_search: Optional[SearchCallable] = None,
    db_path: str | None = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Fuse vector and keyword search with reciprocal rank fusion.

    Args:
        query: free-text search query.
        phase: build phase to restrict results to, or None for no filter.
        limit: maximum number of fused results.
        vector_search: txtai-backed search callable (None skips vector search).
        fts_search: FTS5-backed search callable (defaults to the SQLite gem_fts table).
        db_path: path to SQLite database.
        category: category slug to restrict results to, or None for no filter.

    Returns up to `limit` hits sorted by fused score, each carrying name,
    description, category, layer, phase, source_code_uri, context7_id, score.
    """
    if category:
        categories = [category]
    elif phase:
        categories = categories_for(phase)
    else:
        categories = None

    if categories and not category:
        # Narrow the phase bucket to the categories the query targets so a
        # phase-wide filter does not drag in unrelated categories' gems.
        categories = narrow_categories(query, categories)
    keyword_search = fts_search or make_fts_search(db_path)

    vector_results: list[dict] = []
    if vector_search is not None:
        try:
            vector_results = vector_search(query, categories, limit) or []
        except Exception:
            vector_results = []

    keyword_results: list[dict] = []
    try:
        keyword_results = keyword_search(query, categories, limit) or []
    except Exception:
        keyword_results = []

    fused: dict[str, dict] = {}
    for source, results in (("vector", vector_results), ("keyword", keyword_results)):
        seen: set[str] = set()
        rank = 0
        for hit in results:
            name = hit.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            entry = fused.setdefault(name, _annotate(hit))
            entry["score"] = round(entry.get("score", 0.0) + 1.0 / (RRF_K + rank), 6)
            rank += 1

    # Preferred gems get a soft rank boost: enough to win within their
    # source-count class, never enough to outrank a two-source hit.
    preferred_names = _preferred_names(list(fused), db_path)
    for name, entry in fused.items():
        entry["preferred"] = name in preferred_names
        if entry["preferred"]:
            entry["score"] = round(entry["score"] + PREFERRED_BOOST, 6)

    ranked = sorted(fused.values(), key=lambda h: h["score"], reverse=True)
    return ranked[:limit]


def _preferred_names(names: list[str], db_path: str | None) -> set[str]:
    """Which of the given gems are flagged preferred in the inventory."""
    if not names:
        return set()
    resolved_db = db_path or str(settings.sqlite_db_file)
    placeholders = ", ".join("?" for _ in names)
    try:
        conn = sqlite3.connect(resolved_db, uri=True)
    except sqlite3.Error:
        return set()
    try:
        rows = conn.execute(
            f"SELECT name FROM inventory WHERE preferred = 1 AND name IN ({placeholders})",
            names,
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        conn.close()
    return {row[0] for row in rows}
