"""Automated tests for GemFetcher source resolution, shallow cloning, and fallback unpacking."""

import io
import sqlite3
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rubygemdb.services.gem_fetcher import GemFetcher


@pytest.fixture
def temp_cache_dir(tmp_path: Path):
    cache_dir = tmp_path / "gem_repos"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def mock_db(tmp_path: Path):
    db_file = tmp_path / "test.sqlite"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE inventory (name TEXT, homepage TEXT, source_code_uri TEXT, context7_id TEXT, description TEXT)"
    )
    conn.execute(
        "INSERT INTO inventory VALUES ('thor', 'https://github.com/rails/thor', 'https://github.com/rails/thor/tree/main', 'rails/thor', 'CLI library')"
    )
    conn.execute(
        "INSERT INTO inventory VALUES ('nongit', 'https://example.com', NULL, NULL, 'No git repo')"
    )
    conn.commit()
    conn.close()
    return str(db_file)


def test_normalize_git_url(temp_cache_dir: Path):
    fetcher = GemFetcher(cache_dir=temp_cache_dir)

    assert (
        fetcher._normalize_git_url("https://github.com/rails/thor/tree/main")
        == "https://github.com/rails/thor.git"
    )
    assert (
        fetcher._normalize_git_url("https://gitlab.com/user/project/blob/master/README.md")
        == "https://gitlab.com/user/project.git"
    )
    assert (
        fetcher._normalize_git_url("https://codeberg.org/user/repo")
        == "https://codeberg.org/user/repo.git"
    )
    assert fetcher._normalize_git_url("https://example.com/not-a-repo") is None
    assert fetcher._normalize_git_url("") is None


def test_resolve_source_repo_from_sqlite(temp_cache_dir: Path, mock_db: str):
    fetcher = GemFetcher(cache_dir=temp_cache_dir, db_path=mock_db)
    resolved = fetcher.resolve_source_repo("thor")
    assert resolved == "https://github.com/rails/thor.git"


def test_resolve_source_repo_from_api(temp_cache_dir: Path, tmp_path: Path):
    mock_service = MagicMock()
    mock_service.fetch_gem_info.return_value = {
        "source_code_uri": "https://github.com/socketry/decode"
    }

    empty_db = tmp_path / "empty.sqlite"
    fetcher = GemFetcher(
        cache_dir=temp_cache_dir,
        db_path=str(empty_db),
        rubygems_service=mock_service,
    )
    resolved = fetcher.resolve_source_repo("decode")
    assert resolved == "https://github.com/socketry/decode.git"


def test_clone_repo_success(temp_cache_dir: Path):
    fetcher = GemFetcher(cache_dir=temp_cache_dir)
    dest = temp_cache_dir / "sample_gem"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "clone"], returncode=0, stdout="Cloned", stderr=""
        )
        success = fetcher.clone_repo("https://github.com/user/repo.git", dest)
        assert success is True
        mock_run.assert_called_once()
        assert "--depth" in mock_run.call_args[0][0]
        assert "1" in mock_run.call_args[0][0]


def test_clone_repo_failure(temp_cache_dir: Path):
    fetcher = GemFetcher(cache_dir=temp_cache_dir)
    dest = temp_cache_dir / "failed_gem"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "clone"], returncode=128, stdout="", stderr="fatal: not found"
        )
        success = fetcher.clone_repo("https://github.com/user/invalid.git", dest)
        assert success is False
        assert not dest.exists()


def test_unpack_gem_fallback(temp_cache_dir: Path):
    fetcher = GemFetcher(cache_dir=temp_cache_dir)
    dest = temp_cache_dir / "unpacked_gem"

    # Create synthetic .gem package (tar containing data.tar.gz)
    data_buf = io.BytesIO()
    with tarfile.open(fileobj=data_buf, mode="w:gz") as data_tf:
        content = b"# sample ruby code\nclass Sample\nend\n"
        tarinfo = tarfile.TarInfo(name="lib/sample.rb")
        tarinfo.size = len(content)
        data_tf.addfile(tarinfo, io.BytesIO(content))
    data_bytes = data_buf.getvalue()

    gem_buf = io.BytesIO()
    with tarfile.open(fileobj=gem_buf, mode="w") as gem_tf:
        tarinfo = tarfile.TarInfo(name="data.tar.gz")
        tarinfo.size = len(data_bytes)
        gem_tf.addfile(tarinfo, io.BytesIO(data_bytes))
    gem_bytes = gem_buf.getvalue()

    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.read.return_value = gem_bytes
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp

        success = fetcher.unpack_gem("test_gem", dest, version="1.0.0")
        assert success is True
        assert (dest / "lib" / "sample.rb").exists()
        assert "class Sample" in (dest / "lib" / "sample.rb").read_text()


def test_fetch_gem_uses_cache(temp_cache_dir: Path):
    fetcher = GemFetcher(cache_dir=temp_cache_dir)
    cached = temp_cache_dir / "cached_gem"
    cached.mkdir()
    (cached / "lib").mkdir()
    (cached / "lib" / "test.rb").write_text("class Test; end")

    result = fetcher.fetch_gem("cached_gem")
    assert result == cached


def test_fetch_gem_git_fallback_to_unpack(temp_cache_dir: Path):
    fetcher = GemFetcher(cache_dir=temp_cache_dir)
    target_dir = temp_cache_dir / "fallback_gem"

    with patch.object(fetcher, "resolve_source_repo", return_value="https://github.com/bad/repo.git"):
        with patch.object(fetcher, "clone_repo", return_value=False):
            with patch.object(fetcher, "unpack_gem") as mock_unpack:
                def fake_unpack(name, d, version=None):
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "lib.rb").write_text("# code")
                    return True
                mock_unpack.side_effect = fake_unpack

                res = fetcher.fetch_gem("fallback_gem")
                assert res == target_dir
                mock_unpack.assert_called_once()
