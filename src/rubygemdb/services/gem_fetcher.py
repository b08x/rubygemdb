"""Gem repository fetcher and source caching service with fallback.

Resolves Git source repositories from database metadata and RubyGems API,
performing shallow clones (--depth 1) into data/cache/gem_repos/<gem_name>.
If git resolution or cloning fails, falls back to downloading and unpacking
the released .gem archive.
"""

import io
import logging
import re
import shutil
import sqlite3
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Optional

from rubygemdb.core.config import settings
from rubygemdb.services.rubygems import RubyGemsService

logger = logging.getLogger("rubygemdb.gem_fetcher")


class GemFetcher:
    """Manages cloning, caching, and fallback unpacking for gem source code."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        db_path: Optional[str] = None,
        rubygems_service: Optional[RubyGemsService] = None,
    ):
        self.cache_dir = cache_dir or settings.gem_repos_dir
        self.db_path = db_path or str(settings.sqlite_db_file)
        self.rubygems_service = rubygems_service or RubyGemsService()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_git_url(self, url: str) -> Optional[str]:
        """Normalize repository URLs to a cloneable git URI."""
        if not url:
            return None
        cleaned = url.strip()

        # Check for Git hosts
        git_hosts = ("github.com", "gitlab.com", "codeberg.org", "bitbucket.org")
        if not any(host in cleaned for host in git_hosts) and not cleaned.endswith(".git"):
            return None

        # Clean trailing tree/blob/issues path segments often present in metadata
        match = re.search(r"(https?://[^/]+/[^/]+/[^/]+?)(?:/(?:tree|blob|issues|pulls|commit).*|\.git)?/?$", cleaned)
        if match:
            base_url = match.group(1).rstrip("/")
            return f"{base_url}.git"

        if cleaned.startswith("git@") or cleaned.endswith(".git"):
            return cleaned

        return None

    def resolve_source_repo(self, gem_name: str) -> Optional[str]:
        """Resolve the git repository URL from SQLite inventory or RubyGems API."""
        # 1. Check local SQLite inventory
        try:
            if Path(self.db_path).exists():
                conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT source_code_uri, homepage FROM inventory WHERE name = ? COLLATE NOCASE",
                    (gem_name,),
                )
                row = cursor.fetchone()
                conn.close()

                if row:
                    for field in ("source_code_uri", "homepage"):
                        val = row[field]
                        if val:
                            norm = self._normalize_git_url(val)
                            if norm:
                                return norm
        except Exception as e:
            logger.debug("Error checking SQLite inventory for %s: %s", gem_name, e)

        # 2. Check RubyGems API
        info = self.rubygems_service.fetch_gem_info(gem_name)
        if info:
            for key in ("source_code_uri", "homepage_uri", "project_uri", "bug_tracker_uri"):
                val = info.get(key)
                if val:
                    norm = self._normalize_git_url(str(val))
                    if norm:
                        return norm

        return None

    def clone_repo(self, git_url: str, dest_dir: Path) -> bool:
        """Perform a shallow clone (--depth 1) of the git repository into dest_dir."""
        try:
            if dest_dir.exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
            dest_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                "git",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                git_url,
                str(dest_dir),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info("Successfully shallow-cloned %s into %s", git_url, dest_dir)
                return True

            logger.warning(
                "git clone failed for %s (exit %d): %s",
                git_url,
                result.returncode,
                result.stderr.strip(),
            )
            shutil.rmtree(dest_dir, ignore_errors=True)
            return False
        except Exception as e:
            logger.warning("git clone exception for %s: %s", git_url, e)
            shutil.rmtree(dest_dir, ignore_errors=True)
            return False

    def unpack_gem(self, gem_name: str, dest_dir: Path, version: Optional[str] = None) -> bool:
        """Fallback: download and unpack the released .gem archive into dest_dir."""
        try:
            if not version:
                info = self.rubygems_service.fetch_gem_info(gem_name)
                if info and info.get("version"):
                    version = str(info["version"])

            if not version:
                logger.warning("Cannot determine version to unpack gem %s", gem_name)
                return False

            download_url = f"https://rubygems.org/downloads/{gem_name}-{version}.gem"
            logger.info("Downloading gem package from %s", download_url)

            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "RubyGemDB/1.0 (+https://github.com)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                gem_bytes = resp.read()

            if dest_dir.exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
            dest_dir.mkdir(parents=True, exist_ok=True)

            # A .gem package is a POSIX tar archive containing data.tar.gz
            with tarfile.open(fileobj=io.BytesIO(gem_bytes)) as tf:
                data_member = None
                for member in tf.getmembers():
                    if member.name in ("data.tar.gz", "data.tar"):
                        data_member = member
                        break

                if data_member is None:
                    logger.warning("No data.tar.gz found in %s package", gem_name)
                    return False

                data_file = tf.extractfile(data_member)
                if data_file is None:
                    return False

                data_bytes = data_file.read()
                with tarfile.open(fileobj=io.BytesIO(data_bytes), mode="r:*") as data_tf:
                    data_tf.extractall(path=dest_dir)

            logger.info("Successfully unpacked %s-%s into %s", gem_name, version, dest_dir)
            return True
        except Exception as e:
            logger.warning("Failed to unpack gem %s: %s", gem_name, e)
            shutil.rmtree(dest_dir, ignore_errors=True)
            return False

    def fetch_gem(self, gem_name: str, force_refresh: bool = False) -> Path:
        """Ensure a gem source tree is cached and return its directory path.

        Attempts shallow git clone first, falling back to .gem archive unpack.
        """
        gem_dir = self.cache_dir / gem_name

        # If cache exists and has files, return it unless force_refresh is requested
        if gem_dir.is_dir() and not force_refresh:
            contents = list(gem_dir.glob("*.rb")) + list(gem_dir.glob("lib/**/*.rb"))
            if contents or any(gem_dir.iterdir()):
                return gem_dir

        # 1. Try shallow git clone
        repo_url = self.resolve_source_repo(gem_name)
        if repo_url:
            success = self.clone_repo(repo_url, gem_dir)
            if success:
                return gem_dir

        # 2. Fall back to gem unpack
        success = self.unpack_gem(gem_name, gem_dir)
        if success:
            return gem_dir

        raise RuntimeError(
            f"Failed to fetch source for gem '{gem_name}' via git clone and .gem unpack fallback."
        )
