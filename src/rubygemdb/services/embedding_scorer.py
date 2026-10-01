"""Embedding-assisted category scoring.

Scores a gem's "{name}: {description}" text against precomputed
category-description vectors using txtai in similarity mode, with the same
model as agent.py (ollama/embeddinggemma). If ollama/txtai is unavailable
the scorer degrades gracefully: `score()` returns an empty mapping and the
classifier falls back to keyword + description scoring only.
"""

import logging
import os
from typing import Dict, Optional

from rubygemdb.core.config import settings
from rubygemdb.models.categories import CATEGORIES

logger = logging.getLogger(__name__)


class EmbeddingScorer:
    """Computes slug -> similarity score (0..1) for a gem text."""

    def __init__(self, model_path: str = "ollama/embeddinggemma", cache_dir: Optional[str] = None):
        self.model_path = model_path
        self.cache_dir = cache_dir or str(settings.cache_dir / "category_vectors")
        self._embeddings = None
        self._failed = False

    def _ensure_index(self) -> bool:
        """Lazily build (or load) the category vector index. Returns False on failure."""
        if self._embeddings is not None:
            return True
        if self._failed:
            return False

        try:
            from txtai import Embeddings  # type: ignore

            self.embeddings_cls = Embeddings
            os.makedirs(self.cache_dir, exist_ok=True)
            index_path = os.path.join(self.cache_dir, "category_index")

            embeddings = Embeddings(path=self.model_path, content=True, gpu=settings.txtai_gpu)
            if os.path.exists(index_path):
                embeddings.load(index_path)
            else:
                docs = [
                    {"id": c.slug, "text": f"{c.slug}: {c.description}"}
                    for c in CATEGORIES
                ]
                embeddings.index(docs)
                embeddings.save(index_path)
            self._embeddings = embeddings
            return True
        except Exception as exc:  # ollama down, txtai missing, model fetch failure
            logger.warning("Embedding scorer unavailable, falling back to keyword scoring: %s", exc)
            self._failed = True
            return False

    def score(self, name: str, description: str) -> Dict[str, float]:
        """Return {category_slug: similarity} for the gem. Empty dict when unavailable."""
        if not self._ensure_index():
            return {}

        text = f"{name}: {description or ''}".strip()
        if not text:
            return {}

        try:
            results = self._embeddings.search(text, len(CATEGORIES))  # type: ignore[attr-defined, union-attr]
            return {r["id"]: float(r["score"]) for r in results}
        except Exception as exc:
            logger.warning("Embedding scoring failed for '%s': %s", name, exc)
            self._failed = True
            return {}
