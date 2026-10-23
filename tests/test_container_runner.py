"""Automated tests for ContainerRunner service."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rubygemdb.services.container_runner import ContainerRunner


@pytest.fixture
def mock_runner(tmp_path: Path):
    dockerfile = tmp_path / "indexer.Dockerfile"
    dockerfile.write_text("FROM ruby:3.3-slim\n")
    return ContainerRunner(
        runtime="podman",
        dockerfile_path=dockerfile,
        build_context=tmp_path,
    )


def test_detect_runtime_podman():
    with patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: "/usr/bin/podman" if cmd == "podman" else None
        assert ContainerRunner.detect_runtime() == "podman"


def test_detect_runtime_docker_fallback():
    with patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None
        assert ContainerRunner.detect_runtime() == "docker"


def test_detect_runtime_none():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="No container runtime found"):
            ContainerRunner.detect_runtime()


def test_image_exists_true(mock_runner: ContainerRunner):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "images"], returncode=0, stdout="abc12345\n", stderr=""
        )
        assert mock_runner.image_exists() is True


def test_image_exists_false(mock_runner: ContainerRunner):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "images"], returncode=0, stdout="", stderr=""
        )
        assert mock_runner.image_exists() is False


def test_build_image_success(mock_runner: ContainerRunner):
    with patch("subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.stdout = ["STEP 1/3: FROM ruby\n", "Successfully built image\n"]
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        callback_messages = []
        mock_runner.build_image(progress_callback=callback_messages.append)

        assert any("Successfully built" in msg for msg in callback_messages)
        mock_popen.assert_called_once()


def test_build_image_failure(mock_runner: ContainerRunner):
    with patch("subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.stdout = ["Error: step failed\n"]
        proc.wait.return_value = 1
        mock_popen.return_value = proc

        with pytest.raises(RuntimeError, match="Failed to build container image"):
            mock_runner.build_image()


def test_run_indexer_empty_gems(mock_runner: ContainerRunner, tmp_path: Path):
    with pytest.raises(ValueError, match="gem_paths cannot be empty"):
        mock_runner.run_indexer({}, tmp_path)


def test_run_indexer_success(mock_runner: ContainerRunner, tmp_path: Path):
    gem_dir = tmp_path / "thor"
    gem_dir.mkdir()
    out_dir = tmp_path / "out"

    # Pre-create valid graph.json that container would write
    out_dir.mkdir(parents=True, exist_ok=True)
    graph_content = {
        "version": "1.0",
        "gems": ["thor"],
        "nodes": [{"id": "gem:thor", "type": "gem", "name": "thor", "data": {}}],
        "edges": [],
    }
    (out_dir / "graph.json").write_text(json.dumps(graph_content))

    with patch.object(mock_runner, "ensure_image"):
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.communicate.return_value = ("[extract_graph.rb] Done", "")
            proc.returncode = 0
            mock_popen.return_value = proc

            result = mock_runner.run_indexer({"thor": gem_dir}, out_dir)
            assert result["version"] == "1.0"
            assert len(result["nodes"]) == 1
            assert result["nodes"][0]["name"] == "thor"

            # Check that volume mounts were set
            cmd_args = mock_popen.call_args[0][0]
            assert ":ro,z" in " ".join(cmd_args)
            assert ":rw,z" in " ".join(cmd_args)


def test_ensure_image_force_rebuild(mock_runner):
    with patch.object(mock_runner, "image_exists", return_value=True):
        with patch.object(mock_runner, "build_image") as mock_build:
            mock_runner.ensure_image(force_rebuild=False)
            mock_build.assert_not_called()

            mock_runner.ensure_image(force_rebuild=True)
            mock_build.assert_called_once()
