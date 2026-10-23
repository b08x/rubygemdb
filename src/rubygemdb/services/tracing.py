"""Langfuse observability and tracing service for RubyGemDB.

Follows Langfuse best practices for span hierarchy, observation types,
model/token tracking, session grouping, and graceful degradation.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Literal, Optional

from rubygemdb.core.config import settings

logger = logging.getLogger("rubygemdb.tracing")

# Valid Langfuse observation types
ObservationType = Literal[
    "generation",
    "embedding",
    "span",
    "agent",
    "tool",
    "chain",
    "retriever",
    "evaluator",
    "guardrail",
    "event",
]


class NoOpObservation:
    """Null object representation of a Langfuse observation when tracing is disabled."""

    def __init__(self, name: str = "", as_type: str = "span") -> None:
        self.name = name
        self.as_type = as_type
        self.id = ""
        self.trace_id = ""

    def update(self, *args: Any, **kwargs: Any) -> "NoOpObservation":
        return self

    def end(self, *args: Any, **kwargs: Any) -> None:
        pass

    def score(self, *args: Any, **kwargs: Any) -> None:
        pass

    def score_trace(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "NoOpObservation":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class TracingService:
    """Manages Langfuse client lifecycle, observation context, and graceful fallback."""

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._initialized: bool = False

    @property
    def is_enabled(self) -> bool:
        """True if Langfuse is configured and enabled in settings."""
        return settings.is_langfuse_configured

    @property
    def client(self) -> Optional[Any]:
        """Lazily initialize and return the Langfuse client."""
        if not self.is_enabled:
            return None

        if not self._initialized:
            try:
                import langfuse  # type: ignore
                if not hasattr(langfuse, "version") and hasattr(langfuse, "_version"):
                    langfuse.version = langfuse._version  # type: ignore

                from langfuse import get_client  # type: ignore

                self._client = get_client()
                self._initialized = True
                logger.debug(
                    "Langfuse client initialized successfully for host %s",
                    settings.resolved_langfuse_host,
                )
            except Exception as e:
                logger.warning("Failed to initialize Langfuse client: %s", e)
                self._client = None
                self._initialized = True

        return self._client

    def flush(self) -> None:
        """Safely flush buffered spans to the Langfuse backend."""
        if not self.is_enabled or self._client is None:
            return
        try:
            self._client.flush()
        except Exception as e:
            logger.debug("Langfuse flush warning: %s", e)

    @contextmanager
    def trace_observation(
        self,
        name: str,
        as_type: ObservationType = "span",
        input: Any = None,
        output: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        model: Optional[str] = None,
        model_parameters: Optional[Dict[str, Any]] = None,
        usage_details: Optional[Dict[str, int]] = None,
        cost_details: Optional[Dict[str, float]] = None,
        version: Optional[str] = None,
        **kwargs: Any,
    ) -> Generator[Any, None, None]:
        """Creates a contextual Langfuse observation or falls back to NoOpObservation."""
        client = self.client
        if client is None:
            yield NoOpObservation(name=name, as_type=as_type)
            return

        try:
            from langfuse import propagate_attributes  # type: ignore

            clean_meta = {k: v for k, v in metadata.items() if v is not None} if metadata else None

            prop_kwargs: Dict[str, Any] = {}
            if session_id:
                prop_kwargs["session_id"] = str(session_id)
            if user_id:
                prop_kwargs["user_id"] = str(user_id)
            if tags:
                prop_kwargs["tags"] = list(tags)
            if clean_meta:
                prop_kwargs["metadata"] = dict(clean_meta)
            if version:
                prop_kwargs["version"] = str(version)

            obs_kwargs: Dict[str, Any] = {
                "name": name,
                "as_type": as_type,
                "input": input,
                "output": output,
                "metadata": clean_meta,
                "model": model,
                "model_parameters": model_parameters,
                "usage_details": usage_details,
                "cost_details": cost_details,
                "version": version,
            }
            obs_kwargs.update(kwargs)
            clean_obs_kwargs = {k: v for k, v in obs_kwargs.items() if v is not None}
            ctx_manager = client.start_as_current_observation(**clean_obs_kwargs)
        except Exception as e:
            logger.debug("Failed to create Langfuse observation: %s", e)
            yield NoOpObservation(name=name, as_type=as_type)
            return

        if prop_kwargs:
            with propagate_attributes(**prop_kwargs):
                with ctx_manager as obs:
                    try:
                        yield obs
                    except Exception as exc:
                        try:
                            obs.update(level="ERROR", status_message=str(exc))
                        except Exception:
                            pass
                        raise
        else:
            with ctx_manager as obs:
                try:
                    yield obs
                except Exception as exc:
                    try:
                        obs.update(level="ERROR", status_message=str(exc))
                    except Exception:
                        pass
                    raise


# Global singleton instance
_tracing_service = TracingService()


def get_tracing_service() -> TracingService:
    """Returns the global TracingService instance."""
    return _tracing_service


def trace_observation(
    name: str,
    as_type: ObservationType = "span",
    input: Any = None,
    output: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    model: Optional[str] = None,
    model_parameters: Optional[Dict[str, Any]] = None,
    usage_details: Optional[Dict[str, int]] = None,
    cost_details: Optional[Dict[str, float]] = None,
    version: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Context manager for tracing an observation with Langfuse."""
    return _tracing_service.trace_observation(
        name=name,
        as_type=as_type,
        input=input,
        output=output,
        metadata=metadata,
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        model=model,
        model_parameters=model_parameters,
        usage_details=usage_details,
        cost_details=cost_details,
        version=version,
        **kwargs,
    )


def trace_span(name: str, **kwargs: Any) -> Any:
    """Context manager for tracing a general span."""
    return trace_observation(name=name, as_type="span", **kwargs)


def trace_generation(name: str, model: Optional[str] = None, **kwargs: Any) -> Any:
    """Context manager for tracing an LLM generation."""
    return trace_observation(name=name, as_type="generation", model=model, **kwargs)


def trace_agent(name: str, **kwargs: Any) -> Any:
    """Context manager for tracing an agent or orchestrator turn."""
    return trace_observation(name=name, as_type="agent", **kwargs)


def trace_retriever(name: str, **kwargs: Any) -> Any:
    """Context manager for tracing search or RAG retrieval."""
    return trace_observation(name=name, as_type="retriever", **kwargs)


def trace_tool(name: str, **kwargs: Any) -> Any:
    """Context manager for tracing a tool invocation."""
    return trace_observation(name=name, as_type="tool", **kwargs)


def flush_tracing() -> None:
    """Flushes active traces to the Langfuse backend."""
    _tracing_service.flush()
