"""Automated tests for universal EmbeddingCache and wrap_embeddings."""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

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
