"""Local GGUF-based query expansion service.

Uses mradermacher/qmd-query-expansion-1.7B-GGUF via llama_cpp to generate
lexical and vector variations of search queries for multi-pass retrieval.
"""

import logging
import re
from typing import List, Optional
from pydantic import BaseModel, Field

from rubygemdb.core.config import settings
from rubygemdb.services.model_manager import GGUFModelManager, default_model_manager

logger = logging.getLogger("rubygemdb.query_expander")


class QueryExpansion(BaseModel):
    """Structured query expansion result."""

    lexical: List[str] = Field(default_factory=list)
    vector: List[str] = Field(default_factory=list)
    raw: str = ""


class QueryExpander:
    """Expands search queries into lexical and vector variations using a local GGUF model."""

    PROMPT_TEMPLATE = (
        "<|im_start|>user /no_think Expand this search query: {query}<|im_end|>\n"
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
        return bool(settings.query_expansion_enabled)

    def expand(self, query: str) -> QueryExpansion:
        """Expand a query string into lexical and vector terms.

        Falls back gracefully to an empty QueryExpansion on failure or when disabled.
        """
        clean_query = (query or "").strip()
        if not clean_query or not self.is_enabled:
            return QueryExpansion(lexical=[], vector=[], raw="")

        try:
            model = self.model_manager.get_expander_model()
            if model is None:
                return QueryExpansion(lexical=[], vector=[], raw="")

            prompt = self.PROMPT_TEMPLATE.format(query=clean_query)
            response = model.create_completion(
                prompt=prompt,
                max_tokens=128,
                temperature=0.1,
                stop=["<|im_end|>"],
            )

            raw_text = ""
            if response and "choices" in response and len(response["choices"]) > 0:
                raw_text = response["choices"][0].get("text", "") or ""

            return self.parse_expansion(raw_text)
        except Exception as e:
            logger.warning("Query expansion failed for query '%s': %s", clean_query, e)
            return QueryExpansion(lexical=[], vector=[], raw="")

    @staticmethod
    def parse_expansion(raw_text: str) -> QueryExpansion:
        """Parse raw model output into lexical and vector lists."""
        lexical: List[str] = []
        vector: List[str] = []

        if not raw_text:
            return QueryExpansion(lexical=[], vector=[], raw="")

        for line in raw_text.splitlines():
            clean = line.strip()
            # Strip markdown list markers if present
            clean = re.sub(r"^[-*•\d.]+\s*", "", clean).strip()
            if not clean:
                continue

            lower = clean.lower()
            if lower.startswith("lex:") or lower.startswith("lexical:"):
                content = clean.split(":", 1)[1].strip()
                if content:
                    parts = [p.strip() for p in content.split(",") if p.strip()]
                    for part in parts:
                        if part not in lexical:
                            lexical.append(part)
            elif lower.startswith("vec:") or lower.startswith("vector:"):
                content = clean.split(":", 1)[1].strip()
                if content:
                    parts = [p.strip() for p in content.split(",") if p.strip()]
                    for part in parts:
                        if part not in vector:
                            vector.append(part)

        return QueryExpansion(lexical=lexical, vector=vector, raw=raw_text)


default_query_expander = QueryExpander()
