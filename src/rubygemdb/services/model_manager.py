"""GGUF Model Manager for downloading and caching local llama_cpp models.

Handles lazy downloading from Hugging Face Hub using huggingface_hub.hf_hub_download
and instantiates/caches active Llama instances with graceful degradation.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from rubygemdb.core.config import settings

logger = logging.getLogger("rubygemdb.model_manager")


class GGUFModelManager:
    """Manages on-demand downloading and caching of GGUF models via llama_cpp."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or settings.resolved_models_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._instances: Dict[Tuple[str, str, int, Optional[int]], Any] = {}

    def download_model(self, repo_id: str, filename: str) -> Optional[Path]:
        """Download or resolve a GGUF model file from Hugging Face Hub.

        Returns the Path to the local file, or None if downloading fails.
        """
        try:
            from huggingface_hub import hf_hub_download

            local_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=str(self.cache_dir),
            )
            return Path(local_path)
        except Exception as e:
            logger.warning(
                "Failed to download GGUF model %s/%s from Hugging Face: %s",
                repo_id,
                filename,
                e,
            )
            return None

    def get_llama(
        self,
        repo_id: str,
        filename: str,
        n_ctx: int = 1024,
        n_threads: Optional[int] = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> Optional[Any]:
        """Get or instantiate a cached llama_cpp.Llama instance.

        Returns None if downloading or loading fails.
        """
        cache_key = (repo_id, filename, n_ctx, n_threads)
        if cache_key in self._instances:
            return self._instances[cache_key]

        model_path = self.download_model(repo_id, filename)
        if model_path is None or not model_path.exists():
            return None

        try:
            import llama_cpp

            init_kwargs: Dict[str, Any] = {
                "model_path": str(model_path),
                "n_ctx": n_ctx,
                "verbose": verbose,
            }
            if n_threads is not None:
                init_kwargs["n_threads"] = n_threads
            init_kwargs.update(kwargs)

            instance = llama_cpp.Llama(**init_kwargs)
            self._instances[cache_key] = instance
            return instance
        except Exception as e:
            logger.warning(
                "Failed to load Llama model from %s (%s/%s): %s",
                model_path,
                repo_id,
                filename,
                e,
            )
            return None

    def get_expander_model(self) -> Optional[Any]:
        """Get the configured Query Expander Llama instance."""
        return self.get_llama(
            repo_id=settings.query_expander_repo_id,
            filename=settings.query_expander_filename,
            n_ctx=settings.query_expander_n_ctx,
            n_threads=settings.query_expander_n_threads,
        )

    def get_reranker_model(self) -> Optional[Any]:
        """Get the configured Cross-Encoder Reranker Llama instance."""
        return self.get_llama(
            repo_id=settings.reranker_repo_id,
            filename=settings.reranker_filename,
            n_ctx=settings.reranker_n_ctx,
            n_threads=settings.reranker_n_threads,
        )

    def clear_cache(self) -> None:
        """Clear all loaded in-memory Llama instances."""
        self._instances.clear()


default_model_manager = GGUFModelManager()
