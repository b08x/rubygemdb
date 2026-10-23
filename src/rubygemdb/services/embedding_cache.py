"""Universal content-addressable vector embedding cache.

Stores raw floating-point vector embeddings in an SQLite database keyed by
sha256(model_path + ":" + text), allowing instantaneous cache hits across
GraphRAG runs, gem inventory vectorization, and session exploration.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from rubygemdb.core.config import settings

logger = logging.getLogger("rubygemdb.embedding_cache")


def compute_content_hash(model: str, text: str) -> str:
    """Generate deterministic SHA-256 hash for a (model, text) pair."""
    normalized_model = (model or "").strip()
    normalized_text = (text or "").strip()
    key = f"{normalized_model}:{normalized_text}".encode("utf-8")
    return hashlib.sha256(key).hexdigest()


class EmbeddingCache:
    """SQLite-backed persistent vector embedding cache."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        self.db_path = Path(db_path or settings.cache_dir / "embeddings_cache.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create an optimized SQLite connection with WAL mode."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the embedding_cache schema if not present."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    content_hash TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_embedding_model ON embedding_cache(model);"
            )

    def get(self, text: str, model: str) -> Optional[np.ndarray]:
        """Retrieve a single precomputed vector embedding, or None on cache miss."""
        h = compute_content_hash(model, text)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT dimensions, vector FROM embedding_cache WHERE content_hash = ? AND model = ?",
                (h, model),
            )
            row = cursor.fetchone()
            if row:
                dims = row["dimensions"]
                blob = row["vector"]
                arr = np.frombuffer(blob, dtype=np.float32)
                return arr.reshape((dims,)) if dims and arr.size == dims else arr
        return None

    def get_many(
        self, texts: Sequence[str], model: str
    ) -> Tuple[Dict[int, np.ndarray], List[Tuple[int, str]]]:
        """Batch lookup for a sequence of texts.

        Returns:
            hits: Dict mapping original text index -> np.ndarray vector
            misses: List of tuples (original index, text) for uncached items
        """
        if not texts:
            return {}, []

        indexed_hashes: List[Tuple[int, str, str]] = [
            (idx, text, compute_content_hash(model, text))
            for idx, text in enumerate(texts)
        ]

        hits: Dict[int, np.ndarray] = {}
        found_hashes: Dict[str, np.ndarray] = {}

        # Query in chunks of 500 to stay well under SQLite parameter limits
        chunk_size = 500
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for i in range(0, len(indexed_hashes), chunk_size):
                chunk = indexed_hashes[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                query = f"""
                    SELECT content_hash, dimensions, vector 
                    FROM embedding_cache 
                    WHERE model = ? AND content_hash IN ({placeholders})
                """
                params = [model] + [h for _, _, h in chunk]
                cursor.execute(query, params)
                for row in cursor.fetchall():
                    h = row["content_hash"]
                    dims = row["dimensions"]
                    blob = row["vector"]
                    arr = np.frombuffer(blob, dtype=np.float32)
                    found_hashes[h] = arr.reshape((dims,)) if dims and arr.size == dims else arr

        misses: List[Tuple[int, str]] = []
        for idx, text, h in indexed_hashes:
            if h in found_hashes:
                hits[idx] = found_hashes[h]
            else:
                misses.append((idx, text))

        return hits, misses

    def put(self, text: str, model: str, vector: np.ndarray) -> None:
        """Store a single computed vector embedding."""
        h = compute_content_hash(model, text)
        vec_arr = np.asarray(vector, dtype=np.float32)
        blob = vec_arr.tobytes()
        dims = int(vec_arr.size)
        now = time.time()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO embedding_cache (content_hash, model, dimensions, vector, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (h, model, dims, blob, now),
            )

    def put_many(
        self, items: Sequence[Tuple[str, np.ndarray]], model: str
    ) -> None:
        """Store multiple computed vector embeddings in a single transaction."""
        if not items:
            return

        now = time.time()
        records = []
        for text, vector in items:
            h = compute_content_hash(model, text)
            vec_arr = np.asarray(vector, dtype=np.float32)
            records.append((h, model, int(vec_arr.size), vec_arr.tobytes(), now))

        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO embedding_cache (content_hash, model, dimensions, vector, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )

    def stats(self, model: Optional[str] = None) -> Dict[str, Any]:
        """Return cache statistics (entry count, storage size, models)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if model:
                cursor.execute(
                    "SELECT COUNT(*), SUM(LENGTH(vector)) FROM embedding_cache WHERE model = ?",
                    (model,),
                )
                row = cursor.fetchone()
                count = row[0] or 0
                bytes_used = row[1] or 0
                models = [model]
            else:
                cursor.execute(
                    "SELECT COUNT(*), SUM(LENGTH(vector)) FROM embedding_cache"
                )
                row = cursor.fetchone()
                count = row[0] or 0
                bytes_used = row[1] or 0

                cursor.execute("SELECT DISTINCT model FROM embedding_cache")
                models = [r[0] for r in cursor.fetchall()]

        return {
            "total_vectors": count,
            "approx_vector_bytes": bytes_used,
            "models": models,
            "db_file": str(self.db_path),
        }

    def clear(self, model: Optional[str] = None) -> int:
        """Delete entries from the cache. Returns number of rows deleted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if model:
                cursor.execute("DELETE FROM embedding_cache WHERE model = ?", (model,))
            else:
                cursor.execute("DELETE FROM embedding_cache")
            return cursor.rowcount


_default_cache: Optional[EmbeddingCache] = None


def get_default_embedding_cache() -> EmbeddingCache:
    """Singleton getter for the default global EmbeddingCache."""
    global _default_cache
    if _default_cache is None:
        _default_cache = EmbeddingCache()
    return _default_cache


def wrap_embeddings(
    embeddings: Any,
    model_name: Optional[str] = None,
    cache: Optional[EmbeddingCache] = None,
) -> Any:
    """Wrap a txtai Embeddings instance to intercept encode() with the embedding cache.

    Transparently intercepts all vector generation calls during indexing, upserting,
    and querying. Cache hits completely bypass the underlying neural model, while
    cache misses immediately write through to SQLite.
    """
    if getattr(embeddings, "_embedding_cache_wrapped", None) is True:
        return embeddings

    if not hasattr(embeddings, "model") or not hasattr(embeddings.model, "encode"):
        return embeddings

    active_cache = cache or get_default_embedding_cache()
    resolved_model = (
        model_name
        or getattr(embeddings.model, "path", None)
        or embeddings.config.get("path")
        or settings.embedding_model_path
    )

    original_encode = embeddings.model.encode
    cache_stats = {"hits": 0, "misses": 0, "total_texts": 0}

    @functools.wraps(original_encode)
    def caching_encode(data: Any, category: Any = None) -> np.ndarray:
        # If input is not a list/sequence of texts, pass through directly
        if not isinstance(data, (list, tuple)) or not data:
            return original_encode(data, category)

        # Check if first element is string (text) or already an array
        first_elem = data[0]
        if not isinstance(first_elem, str):
            return original_encode(data, category)

        cache_stats["total_texts"] += len(data)

        # Look up cached vectors in SQLite
        hits, misses = active_cache.get_many(data, resolved_model)
        cache_stats["hits"] += len(hits)
        cache_stats["misses"] += len(misses)

        # 1. All items were cached!
        if not misses:
            dim = hits[0].shape[0] if 0 in hits else 768
            ordered_vecs = [hits[i] for i in range(len(data))]
            return np.array(ordered_vecs, dtype=np.float32)

        # 2. Some (or all) items were missed: encode only the misses
        miss_texts = [text for _, text in misses]
        fresh_vectors = original_encode(miss_texts, category)
        fresh_arr = np.asarray(fresh_vectors, dtype=np.float32)

        # Store newly encoded items into cache in bulk
        items_to_cache = [
            (text, fresh_arr[m_idx])
            for m_idx, (_, text) in enumerate(misses)
        ]
        active_cache.put_many(items_to_cache, resolved_model)

        # Combine hits and misses in the exact original order
        total_len = len(data)
        dim = fresh_arr.shape[1] if fresh_arr.ndim > 1 else fresh_arr.shape[0]
        combined = np.empty((total_len, dim), dtype=np.float32)

        for idx, vec in hits.items():
            combined[idx] = vec

        for m_idx, (orig_idx, _) in enumerate(misses):
            combined[orig_idx] = fresh_arr[m_idx]

        return combined

    embeddings.model.encode = caching_encode
    embeddings._embedding_cache_wrapped = True
    embeddings._embedding_cache = active_cache
    embeddings._embedding_cache_stats = cache_stats
    embeddings._embedding_model_name = resolved_model

    logger.info("Wrapped txtai embeddings model '%s' with SQLite embedding cache", resolved_model)
    return embeddings
