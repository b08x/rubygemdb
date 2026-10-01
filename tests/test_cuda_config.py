"""Tests for optional CUDA configuration and GPU support."""

from unittest.mock import MagicMock, patch

from rubygemdb.core.config import Settings
from rubygemdb.services.embedding_scorer import EmbeddingScorer
from rubygemdb.services.stack import StackService


def test_settings_cuda_auto_detection():
    """Verify is_cuda_available respects torch.cuda.is_available() by default."""
    s = Settings()
    with patch("torch.cuda.is_available", return_value=True):
        assert s.is_cuda_available is True
        assert s.txtai_gpu is True

    with patch("torch.cuda.is_available", return_value=False):
        assert s.is_cuda_available is False
        assert s.txtai_gpu is False


def test_settings_cuda_explicit_override():
    """Explicit cuda_enabled=False forces GPU off even if hardware/CUDA is available."""
    s = Settings(cuda_enabled=False)
    with patch("torch.cuda.is_available", return_value=True):
        assert s.is_cuda_available is False
        assert s.txtai_gpu is False

    s2 = Settings(cuda_enabled=True)
    with patch("torch.cuda.is_available", return_value=True):
        assert s2.is_cuda_available is True
        assert s2.txtai_gpu is True


def test_settings_custom_device():
    """Setting explicit device passes device string to txtai_gpu."""
    s = Settings(device="cuda:1")
    assert s.txtai_gpu == "cuda:1"

    # If cuda_enabled is explicitly False, device is overridden to False
    s_disabled = Settings(device="cuda:1", cuda_enabled=False)
    assert s_disabled.txtai_gpu is False


def test_embedding_scorer_passes_gpu_to_txtai(tmp_path):
    """EmbeddingScorer initializes Embeddings with gpu setting."""
    with patch("txtai.Embeddings") as mock_embeddings:
        scorer = EmbeddingScorer(cache_dir=str(tmp_path))
        with patch("rubygemdb.core.config.settings.cuda_enabled", False):
            scorer._ensure_index()
            assert mock_embeddings.call_count == 1
            _, kwargs = mock_embeddings.call_args
            assert kwargs.get("gpu") is False


def test_stack_service_passes_gpu_to_txtai(tmp_path):
    """StackService._load_index initializes Embeddings with gpu setting."""
    index_file = tmp_path / "rubygems_index"
    index_file.mkdir()

    with patch("rubygemdb.core.config.settings.txtai_dir", tmp_path), \
         patch("rubygemdb.core.config.settings.cuda_enabled", False), \
         patch("txtai.Embeddings") as mock_embeddings:
        mock_instance = MagicMock()
        mock_embeddings.return_value = mock_instance
        service = StackService()
        idx = service._load_index()
        assert idx == mock_instance
        _, kwargs = mock_embeddings.call_args
        assert kwargs.get("gpu") is False


def test_cli_cuda_cpu_flags():
    """CLI flags update settings.cuda_enabled accordingly."""
    from rubygemdb.cli import run_cli
    from rubygemdb.core.config import settings

    with patch("sys.argv", ["rubygemdb", "--cuda", "stack", "cli"]):
        with patch("rubygemdb.cli.run_stack"):
            run_cli()
            assert settings.cuda_enabled is True

    with patch("sys.argv", ["rubygemdb", "--cpu", "stack", "cli"]):
        with patch("rubygemdb.cli.run_stack"):
            run_cli()
            assert settings.cuda_enabled is False
