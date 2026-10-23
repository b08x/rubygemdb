"""Cross-Encoder Reranker service using local GGUF models via llama_cpp.

Calculates cross-attention relevance scores between queries and candidate documents
using ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF to calibrate and re-order search results.
"""

import logging
import math
from typing import Any, Callable, Dict, List, Optional

from rubygemdb.core.config import settings
from rubygemdb.services.model_manager import GGUFModelManager, default_model_manager

logger = logging.getLogger("rubygemdb.reranker")


class CrossEncoderReranker:
    """Reranks candidate passages using local Qwen3 cross-encoder GGUF model."""

    PROMPT_TEMPLATE = (
        "<|im_start|>system\n"
        "Judge whether the Document meets the requirements based on the Query.<|im_end|>\n"
        "<|im_start|>user\n"
        "<Query>: {query}\n"
        "<Document>: {passage}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )

    def __init__(
        self,
        model_manager: Optional[GGUFModelManager] = None,
        enabled: Optional[bool] = None,
    ):
        self.model_manager = model_manager or default_model_manager
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        return bool(settings.reranker_enabled)

    def score_pair(self, query: str, passage: str, model: Optional[Any] = None) -> float:
        """Compute relevance score for a single (query, passage) pair.

        Returns float score between 0.0 and 1.0.
        """
        clean_query = (query or "").strip()
        clean_passage = (passage or "").strip()
        if not clean_query or not clean_passage:
            return 0.0

        active_model = model or self.model_manager.get_reranker_model()
        if active_model is None:
            return 0.0

        prompt = self.PROMPT_TEMPLATE.format(query=clean_query, passage=clean_passage)
        response = active_model.create_completion(
            prompt=prompt,
            max_tokens=1,
            temperature=0.0,
            logprobs=10,
        )

        if not response or "choices" not in response or not response["choices"]:
            return 0.0

        choice = response["choices"][0]
        logprobs_data = choice.get("logprobs")

        if logprobs_data:
            # In llama-cpp-python, top_logprobs can be a list of dicts [{token: logprob}]
            # or a dict {token: logprob}
            top_dict = {}
            if isinstance(logprobs_data, dict):
                tl = logprobs_data.get("top_logprobs")
                if isinstance(tl, list) and len(tl) > 0 and isinstance(tl[0], dict):
                    top_dict = tl[0]
                elif isinstance(tl, dict):
                    top_dict = tl
                else:
                    top_dict = logprobs_data

            yes_logprob: Optional[float] = None
            for token, lp in top_dict.items():
                if isinstance(token, str) and token.strip().lower() == "yes":
                    if yes_logprob is None or lp > yes_logprob:
                        yes_logprob = float(lp)

            if yes_logprob is not None:
                # Clip to prevent overflow/underflow
                return max(0.0, min(1.0, math.exp(yes_logprob)))

        # Fallback to generated token text if logprobs unavailable
        text = (choice.get("text") or "").strip().lower()
        if text.startswith("yes"):
            return 1.0
        return 0.0

    def rerank(
        self,
        query: str,
        items: List[Dict[str, Any]],
        text_extractor: Optional[Callable[[Dict[str, Any]], str]] = None,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Rerank items using cross-encoder scores against the query.

        Args:
            query: User search query.
            items: Candidate dictionaries.
            text_extractor: Function mapping item dict to string passage for scoring.
            top_k: Optional maximum number of items to return after reranking.

        Returns items ordered by rerank_score descending.
        """
        if not items:
            return []

        limit = top_k if top_k is not None else len(items)

        if not self.is_enabled:
            return items[:limit]

        try:
            model = self.model_manager.get_reranker_model()
            if model is None:
                return items[:limit]

            extractor = text_extractor or (
                lambda item: f"{item.get('name', '')}: {item.get('description', '')}"
            )

            scored_items = []
            for item in items:
                passage = extractor(item)
                score = self.score_pair(query, passage, model=model)
                updated_item = dict(item)
                updated_item["rerank_score"] = round(score, 6)
                scored_items.append(updated_item)

            scored_items.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)
            return scored_items[:limit]

        except Exception as e:
            logger.warning("Reranking failed, falling back to original ordering: %s", e)
            return items[:limit]


default_reranker = CrossEncoderReranker()
