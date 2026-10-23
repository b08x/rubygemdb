"""Tests for Langfuse observability and tracing integration."""

from unittest.mock import MagicMock, patch
import pytest

from rubygemdb.core.config import Settings
from rubygemdb.services.tracing import (
    NoOpObservation,
    TracingService,
    flush_tracing,
    trace_generation,
)


class TestTracingServiceNoOp:
    """Verifies graceful fallback when Langfuse is disabled or unconfigured."""

    def test_noop_observation_interface(self):
        obs = NoOpObservation(name="test", as_type="span")
        assert obs.name == "test"
        assert obs.as_type == "span"
        assert obs.id == ""
        assert obs.trace_id == ""

        # Verify chainable and safe methods
        assert obs.update(output={"status": "ok"}) is obs
        obs.end()
        obs.score(name="eval", value=1.0)
        obs.score_trace(name="eval", value=1.0)

        # Verify context manager protocol
        with obs as entered:
            assert entered is obs

    def test_disabled_tracing_yields_noop(self, monkeypatch):
        test_settings = Settings(
            langfuse_public_key=None,
            langfuse_secret_key=None,
            langfuse_enabled=False,
        )
        monkeypatch.setattr("rubygemdb.services.tracing.settings", test_settings)

        svc = TracingService()
        assert not svc.is_enabled
        assert svc.client is None

        with svc.trace_observation(name="test-span", as_type="span") as obs:
            assert isinstance(obs, NoOpObservation)
            obs.update(output="anything")

        # Verify flush does not raise
        svc.flush()


class TestTracingServiceEnabled:
    """Verifies behavior when Langfuse is configured and active."""

    @pytest.fixture
    def mock_langfuse_client(self):
        client = MagicMock()
        mock_obs = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_obs
        mock_ctx.__exit__.return_value = None
        client.start_as_current_observation.return_value = mock_ctx
        return client, mock_obs

    def test_trace_observation_creates_span(self, monkeypatch, mock_langfuse_client):
        client, mock_obs = mock_langfuse_client
        test_settings = Settings(
            langfuse_public_key="pk-lf-test",
            langfuse_secret_key="sk-lf-test",
            langfuse_host="http://localhost:3000",
            langfuse_enabled=True,
        )
        monkeypatch.setattr("rubygemdb.services.tracing.settings", test_settings)

        svc = TracingService()
        svc._client = client
        svc._initialized = True

        with svc.trace_observation(
            name="test-op",
            as_type="span",
            input={"query": "test"},
            metadata={"source": "test"},
        ) as obs:
            obs.update(output={"result": 42})

        client.start_as_current_observation.assert_called_once()
        call_kwargs = client.start_as_current_observation.call_args[1]
        assert call_kwargs["name"] == "test-op"
        assert call_kwargs["as_type"] == "span"
        assert call_kwargs["input"] == {"query": "test"}
        assert call_kwargs["metadata"] == {"source": "test"}

        mock_obs.update.assert_called_with(output={"result": 42})

    def test_trace_generation_tracks_model_and_tokens(self, monkeypatch, mock_langfuse_client):
        client, mock_obs = mock_langfuse_client
        test_settings = Settings(
            langfuse_public_key="pk-lf-test",
            langfuse_secret_key="sk-lf-test",
            langfuse_host="http://localhost:3000",
            langfuse_enabled=True,
        )
        monkeypatch.setattr("rubygemdb.services.tracing.settings", test_settings)

        svc = TracingService()
        svc._client = client
        svc._initialized = True

        monkeypatch.setattr("rubygemdb.services.tracing._tracing_service", svc)

        with trace_generation(
            name="llm-generate",
            model="mistral-small",
            input={"messages": [{"role": "user", "content": "hello"}]},
            usage_details={"input": 10, "output": 20, "total": 30},
        ) as gen:
            gen.update(output={"text": "world"})

        call_kwargs = client.start_as_current_observation.call_args[1]
        assert call_kwargs["name"] == "llm-generate"
        assert call_kwargs["as_type"] == "generation"
        assert call_kwargs["model"] == "mistral-small"
        assert call_kwargs["usage_details"] == {"input": 10, "output": 20, "total": 30}

    def test_trace_captures_error(self, monkeypatch, mock_langfuse_client):
        client, mock_obs = mock_langfuse_client
        test_settings = Settings(
            langfuse_public_key="pk-lf-test",
            langfuse_secret_key="sk-lf-test",
            langfuse_host="http://localhost:3000",
            langfuse_enabled=True,
        )
        monkeypatch.setattr("rubygemdb.services.tracing.settings", test_settings)

        svc = TracingService()
        svc._client = client
        svc._initialized = True

        with pytest.raises(RuntimeError, match="Failure in operation"):
            with svc.trace_observation(name="failing-op", as_type="span"):
                raise RuntimeError("Failure in operation")

        mock_obs.update.assert_called_with(level="ERROR", status_message="Failure in operation")

    def test_session_id_propagation(self, monkeypatch, mock_langfuse_client):
        client, mock_obs = mock_langfuse_client
        test_settings = Settings(
            langfuse_public_key="pk-lf-test",
            langfuse_secret_key="sk-lf-test",
            langfuse_host="http://localhost:3000",
            langfuse_enabled=True,
        )
        monkeypatch.setattr("rubygemdb.services.tracing.settings", test_settings)

        svc = TracingService()
        svc._client = client
        svc._initialized = True

        with patch("langfuse.propagate_attributes") as mock_propagate:
            mock_prop_ctx = MagicMock()
            mock_propagate.return_value = mock_prop_ctx

            with svc.trace_observation(
                name="session-turn",
                as_type="agent",
                session_id="session-1234",
                tags=["rubygemdb", "chat"],
            ):
                pass

            mock_propagate.assert_called_once()
            prop_kwargs = mock_propagate.call_args[1]
            assert prop_kwargs["session_id"] == "session-1234"
            assert prop_kwargs["tags"] == ["rubygemdb", "chat"]

    def test_flush_calls_client_flush(self, monkeypatch, mock_langfuse_client):
        client, _ = mock_langfuse_client
        test_settings = Settings(
            langfuse_public_key="pk-lf-test",
            langfuse_secret_key="sk-lf-test",
            langfuse_host="http://localhost:3000",
            langfuse_enabled=True,
        )
        monkeypatch.setattr("rubygemdb.services.tracing.settings", test_settings)

        svc = TracingService()
        svc._client = client
        svc._initialized = True
        monkeypatch.setattr("rubygemdb.services.tracing._tracing_service", svc)

        flush_tracing()
        client.flush.assert_called_once()
