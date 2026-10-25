"""GitHub Starred Repositories Service.

Fetches paginated starred repositories for a user filtered by language,
and resolves repositories to published RubyGems via repo name matching
or root *.gemspec inspection against rubygems.org.
"""

import logging
import os
import shutil
import subprocess
from typing import Any, List, Optional
import requests

from rubygemdb.core.config import settings
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.context7 import Context7Service

logger = logging.getLogger(__name__)


class GitHubStarsService:
    def __init__(
        self,
        token: Optional[str] = None,
        rubygems_service: Optional[RubyGemsService] = None,
        context7_service: Optional[Context7Service] = None,
    ):
        self.token = token or self._resolve_token()
        self.rubygems = rubygems_service or RubyGemsService()
        self.context7 = context7_service or Context7Service()

    @staticmethod
    def _resolve_token() -> Optional[str]:
        """Resolve GitHub personal access token from settings, env, or gh CLI."""
        token = settings.github_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token and token.strip():
            return token.strip()

        # Fallback to `gh auth token` if gh CLI is installed
        if shutil.which("gh"):
            try:
                proc = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip()
            except Exception as e:
                logger.debug(f"Failed to retrieve token from gh CLI: {e}")

        return None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "RubyGemDB/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fetch_starred_repos(
        self,
        user: str = "b08x",
        language: Optional[str] = "Ruby",
        max_pages: int = 100,
    ) -> List[dict[str, Any]]:
        """Fetch all starred repositories for a GitHub user, filtered by language."""
        starred: List[dict[str, Any]] = []
        page = 1
        lang_target = language.lower() if language and language.lower() not in ("all", "*") else None

        while page <= max_pages:
            url = f"https://api.github.com/users/{user}/starred"
            params = {"per_page": 100, "page": page}
            try:
                r = requests.get(url, headers=self._headers(), params=params, timeout=15)
                if r.status_code == 403 or r.status_code == 429:
                    msg = r.json().get("message", "Rate limit exceeded") if r.text else "Rate limit"
                    logger.warning(f"GitHub API rate limit hit on page {page}: {msg}")
                    break
                if r.status_code != 200:
                    logger.error(f"GitHub API returned HTTP {r.status_code}: {r.text[:200]}")
                    break

                data = r.json()
                if not isinstance(data, list) or not data:
                    break

                for repo in data:
                    repo_lang = (repo.get("language") or "").lower()
                    if lang_target is None or repo_lang == lang_target:
                        starred.append(repo)

                if len(data) < 100:
                    break
                page += 1
            except Exception as e:
                logger.error(f"Error fetching starred repos page {page} for {user}: {e}")
                break

        return starred

    def fetch_repo_contents(self, owner: str, repo: str, path: str = "") -> Optional[List[dict[str, Any]]]:
        """Fetch file listing for a repository directory via GitHub REST API."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}".rstrip("/")
        try:
            r = requests.get(url, headers=self._headers(), timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.debug(f"Error fetching contents for {owner}/{repo}: {e}")
        return None

    def resolve_gem_for_repo(
        self,
        repo: dict[str, Any],
        rubygems_service: Optional[RubyGemsService] = None,
    ) -> Optional[dict[str, Any]]:
        """Resolve a GitHub repository to a published RubyGem on rubygems.org.

        Tests direct repo name first, then inspects root *.gemspec files.
        Returns validated gem dictionary or None if repository is not a published gem.
        """
        rg = rubygems_service or self.rubygems
        owner = repo.get("owner", {}).get("login", "")
        repo_name = repo.get("name", "")
        html_url = repo.get("html_url", "")
        repo_desc = repo.get("description") or ""
        repo_home = repo.get("homepage") or ""

        if not repo_name:
            return None

        # 1. Candidate: repo name directly
        info = rg.fetch_gem_info(repo_name)
        matched_gem_name = repo_name if info else None

        # 2. Candidate: inspect root *.gemspec files if repo name wasn't found
        if not info and owner and repo_name:
            contents = self.fetch_repo_contents(owner, repo_name)
            if contents:
                for item in contents:
                    fname = item.get("name", "")
                    if fname.endswith(".gemspec"):
                        cand = fname[:-8]
                        cand_info = rg.fetch_gem_info(cand)
                        if cand_info:
                            info = cand_info
                            matched_gem_name = cand
                            break

        if not info or not matched_gem_name:
            logger.debug(f"Repository {owner}/{repo_name} not found on rubygems.org, skipping.")
            return None

        # Extract verified gem attributes
        version = info.get("version")
        description = info.get("info") or repo_desc
        homepage = info.get("homepage_uri") or repo_home or html_url
        source_code_uri = info.get("source_code_uri") or html_url

        # Canonicalize Context7 ID
        context7_id = self.context7.extract_repo_id(source_code_uri) or self.context7.extract_repo_id(html_url)

        return {
            "name": matched_gem_name,
            "version": version,
            "description": description,
            "homepage": homepage,
            "source_code_uri": source_code_uri,
            "context7_id": context7_id,
            "github_repo": repo.get("full_name") or f"{owner}/{repo_name}",
            "stars": repo.get("stargazers_count", 0),
        }
