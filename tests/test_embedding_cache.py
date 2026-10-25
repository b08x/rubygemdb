"""Automated tests for universal EmbeddingCache and wrap_embeddings."""

import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from rubygemdb.core.config import settings
from rubygemdb.services.embedding_cache import (
    EmbeddingCache,
    compute_content_hash,
    wrap_embeddings,
)


def test_compute_content_hash():
    h1 = compute_content_hash("ollama/embeddinggemma-2:440m", "def execute(sql)")
    h2 = compute_content_hash("ollama/embeddinggemma-2:440m", "def execute(sql)")
    h3 = compute_content_hash("ollama/embeddinggemma-2:440m", "def different(sql)")
    h4 = compute_content_hash("other-model", "def execute(sql)")

    assert h1 == h2
    assert len(h1) == 64
    assert h1 != h3
    assert h1 != h4


def test_embedding_cache_put_and_get(tmp_path: Path):
    db_file = tmp_path / "test_embeddings.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    model = "test-model"
    text = "Ruby class Sequel::Model"
    vec = np.array([0.1, 0.2, -0.3, 0.4], dtype=np.float32)

    # Cache miss
    assert cache.get(text, model) is None

    # Put and get
    cache.put(text, model, vec)
    cached_vec = cache.get(text, model)

    assert cached_vec is not None
    assert isinstance(cached_vec, np.ndarray)
    assert cached_vec.dtype == np.float32
    assert np.allclose(cached_vec, vec)

    # Different model is a miss
    assert cache.get(text, "different-model") is None


def test_embedding_cache_get_many_and_put_many(tmp_path: Path):
    db_file = tmp_path / "test_embeddings.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    model = "test-model"
    texts = [f"Text chunk {i}" for i in range(10)]
    vecs = [np.array([float(i), float(i * 2)], dtype=np.float32) for i in range(10)]

    # Initially all are misses
    hits, misses = cache.get_many(texts, model)
    assert len(hits) == 0
    assert len(misses) == 10
    assert [idx for idx, _ in misses] == list(range(10))

    # Cache first 5 items
    items_to_put = [(texts[i], vecs[i]) for i in range(5)]
    cache.put_many(items_to_put, model)

    # Now first 5 are hits, last 5 are misses
    hits, misses = cache.get_many(texts, model)
    assert len(hits) == 5
    assert len(misses) == 5
    for i in range(5):
        assert np.allclose(hits[i], vecs[i])
    assert [idx for idx, _ in misses] == [5, 6, 7, 8, 9]

    # Cache the rest
    cache.put_many([(texts[i], vecs[i]) for i in range(5, 10)], model)
    hits, misses = cache.get_many(texts, model)
    assert len(hits) == 10
    assert len(misses) == 0


def test_embedding_cache_stats_and_clear(tmp_path: Path):
    db_file = tmp_path / "test_embeddings.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    cache.put("item 1", "model-a", np.array([1.0, 2.0], dtype=np.float32))
    cache.put("item 2", "model-a", np.array([3.0, 4.0], dtype=np.float32))
    cache.put("item 3", "model-b", np.array([5.0, 6.0], dtype=np.float32))

    stats = cache.stats()
    assert stats["total_vectors"] == 3
    assert set(stats["models"]) == {"model-a", "model-b"}

    stats_a = cache.stats(model="model-a")
    assert stats_a["total_vectors"] == 2

    # Clear only model-a
    deleted = cache.clear(model="model-a")
    assert deleted == 2
    assert cache.stats()["total_vectors"] == 1

    # Clear all
    cache.clear()
    assert cache.stats()["total_vectors"] == 0


def test_wrap_embeddings_caching_lifecycle(tmp_path: Path):
    db_file = tmp_path / "test_embeddings.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    # Create mock txtai Embeddings object
    mock_embeddings = MagicMock()
    mock_model = MagicMock()
    mock_model.path = "ollama/embeddinggemma-2:440m"

    encode_calls = []

    def mock_encode(data, category=None):
        encode_calls.append(list(data))
        # Return synthetic vectors of shape (len(data), 4)
        return np.array([[float(len(t)), 1.0, 2.0, 3.0] for t in data], dtype=np.float32)

    mock_model.encode = mock_encode
    mock_embeddings.model = mock_model
    mock_embeddings.config = {"path": "ollama/embeddinggemma-2:440m"}

    wrapped = wrap_embeddings(mock_embeddings, cache=cache)
    assert getattr(wrapped, "_embedding_cache_wrapped") is True

    # Call 1: 3 new texts (all misses)
    texts_turn_1 = ["Ruby method #1", "Ruby method #2", "Ruby method #3"]
    vecs_1 = wrapped.model.encode(texts_turn_1)
    assert vecs_1.shape == (3, 4)
    assert len(encode_calls) == 1
    assert encode_calls[0] == texts_turn_1
    assert wrapped._embedding_cache_stats["hits"] == 0
    assert wrapped._embedding_cache_stats["misses"] == 3

    # Call 2: Exact same 3 texts (100% cache hits, zero encode calls!)
    vecs_2 = wrapped.model.encode(texts_turn_1)
    assert vecs_2.shape == (3, 4)
    assert np.allclose(vecs_1, vecs_2)
    assert len(encode_calls) == 1  # No new calls to underlying model!
    assert wrapped._embedding_cache_stats["hits"] == 3

    # Call 3: Mix of 1 cached, 2 new, 1 cached
    texts_turn_3 = [
        "Ruby method #1",  # hit
        "New method #4",   # miss
        "Ruby method #3",  # hit
        "New method #5",   # miss
    ]
    vecs_3 = wrapped.model.encode(texts_turn_3)
    assert vecs_3.shape == (4, 4)
    assert len(encode_calls) == 2  # Only 1 additional call for the 2 misses!
    assert encode_calls[1] == ["New method #4", "New method #5"]
    assert np.allclose(vecs_3[0], vecs_1[0])
    assert np.allclose(vecs_3[2], vecs_1[2])


def test_wrap_embeddings_idempotent(tmp_path: Path):
    mock_embeddings = MagicMock()
    mock_model = MagicMock()
    mock_model.encode = lambda data, category=None: np.zeros((len(data), 2))
    mock_embeddings.model = mock_model
    mock_embeddings.config = {}

    wrap_embeddings(mock_embeddings)
    first_encode = mock_embeddings.model.encode

    wrap_embeddings(mock_embeddings)
    assert mock_embeddings.model.encode is first_encode


def test_embedding_cache_default_db_path_reads_settings(tmp_path: Path, monkeypatch):
    # Verify default reads settings.embeddings_cache_file
    cache_default = EmbeddingCache()
    assert cache_default.db_path == settings.embeddings_cache_file

    # Verify modifying settings.embeddings_cache_file updates the default path
    custom_file = tmp_path / "custom_embeddings.sqlite"
    monkeypatch.setattr(settings, "embeddings_cache_file", custom_file)
    cache_custom = EmbeddingCache()
    assert cache_custom.db_path == custom_file


def test_embedding_cache_clear_with_vacuum(tmp_path: Path):
    db_file = tmp_path / "vacuum_test.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    # Insert 50 large vectors to expand database size
    items = [(f"large item {i}", np.ones(1000, dtype=np.float32) * i) for i in range(50)]
    cache.put_many(items, "model-large")

    stats_before = cache.stats()
    assert stats_before["total_vectors"] == 50
    total_bytes_before = stats_before["total_disk_bytes"]
    assert total_bytes_before > 0

    # Clear with vacuum=True
    deleted = cache.clear(vacuum=True)
    assert deleted == 50
    stats_after = cache.stats()
    assert stats_after["total_vectors"] == 0

    total_bytes_after = stats_after["total_disk_bytes"]
    assert total_bytes_after < total_bytes_before


def test_embedding_cache_prune_by_ttl(tmp_path: Path):
    db_file = tmp_path / "ttl_test.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    # Insert 5 items
    items = [(f"item {i}", np.array([float(i)], dtype=np.float32)) for i in range(5)]
    cache.put_many(items, "model-ttl")

    now = time.time()
    # Backdate items 0, 1, 2 to 200 days ago, items 3, 4 to 10 days ago
    conn = cache._get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE embedding_cache SET created_at = ? WHERE content_hash IN (?, ?, ?)",
                (
                    now - (200 * 86400.0),
                    compute_content_hash("model-ttl", "item 0"),
                    compute_content_hash("model-ttl", "item 1"),
                    compute_content_hash("model-ttl", "item 2"),
                ),
            )
            conn.execute(
                "UPDATE embedding_cache SET created_at = ? WHERE content_hash IN (?, ?)",
                (
                    now - (10 * 86400.0),
                    compute_content_hash("model-ttl", "item 3"),
                    compute_content_hash("model-ttl", "item 4"),
                ),
            )
    finally:
        conn.close()

    res = cache.prune(max_age_days=100, max_entries=None, vacuum=False)
    assert res["deleted_expired"] == 3
    assert res["deleted_capacity"] == 0
    assert res["total_deleted"] == 3
    assert res["vacuumed"] is False

    assert cache.stats()["total_vectors"] == 2
    assert cache.get("item 3", "model-ttl") is not None
    assert cache.get("item 4", "model-ttl") is not None
    assert cache.get("item 0", "model-ttl") is None


def test_embedding_cache_prune_by_capacity(tmp_path: Path):
    db_file = tmp_path / "capacity_test.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    items = [(f"cap item {i}", np.array([float(i)], dtype=np.float32)) for i in range(10)]
    cache.put_many(items, "model-cap")

    now = time.time()
    conn = cache._get_connection()
    try:
        with conn:
            for i in range(10):
                conn.execute(
                    "UPDATE embedding_cache SET created_at = ? WHERE content_hash = ?",
                    (now + (i * 10), compute_content_hash("model-cap", f"cap item {i}")),
                )
    finally:
        conn.close()

    # Cap at 4 entries; oldest 6 should be trimmed
    res = cache.prune(max_age_days=None, max_entries=4, vacuum=False)
    assert res["deleted_expired"] == 0
    assert res["deleted_capacity"] == 6
    assert res["total_deleted"] == 6

    assert cache.stats()["total_vectors"] == 4
    # The newest 4 are items 6, 7, 8, 9
    for i in range(6, 10):
        assert cache.get(f"cap item {i}", "model-cap") is not None
    for i in range(6):
        assert cache.get(f"cap item {i}", "model-cap") is None


def test_embedding_cache_opportunistic_inline_pruning(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "embeddings_cache_max_entries", 5)

    db_file = tmp_path / "inline_test.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    # Insert 8 items via put_many
    items = [(f"inline item {i}", np.array([float(i)], dtype=np.float32)) for i in range(8)]
    cache.put_many(items, "model-inline")

    # Should have been opportunistically trimmed to 5 entries
    assert cache.stats()["total_vectors"] == 5


def test_embedding_cache_stats_file_sizes(tmp_path: Path):
    db_file = tmp_path / "sizes_test.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    cache.put("sample text", "m1", np.array([1.0, 2.0, 3.0], dtype=np.float32))
    stats = cache.stats()

    assert stats["db_file_bytes"] > 0
    assert stats["wal_file_bytes"] >= 0
    assert stats["shm_file_bytes"] >= 0
    assert stats["total_disk_bytes"] == stats["db_file_bytes"] + stats["wal_file_bytes"] + stats["shm_file_bytes"]


def test_embedding_cache_prune_auto_vacuum(tmp_path: Path):
    db_file = tmp_path / "auto_vac_test.sqlite"
    cache = EmbeddingCache(db_path=db_file)

    items = [(f"vac item {i}", np.array([float(i)], dtype=np.float32)) for i in range(20)]
    cache.put_many(items, "model-vac")

    # Pruning 1 item out of 20 = 5% <= 10%, auto-vacuum should be False
    res1 = cache.prune(max_age_days=None, max_entries=19, vacuum=None)
    assert res1["total_deleted"] == 1
    assert res1["vacuumed"] is False

    # Pruning 10 items out of 19 > 10%, auto-vacuum should be True
    res2 = cache.prune(max_age_days=None, max_entries=9, vacuum=None)
    assert res2["total_deleted"] == 10
    assert res2["vacuumed"] is True
