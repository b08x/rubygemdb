import time
import re
import requests
from typing import Optional
from rubygemdb.core.config import settings

class Context7Service:
    def __init__(self):
        self.api_key = settings.context7_api_key
        self._last_request_time = 0.0
        self.rate_limit_delay = 0.2  # 0.2s for context7 just to be safe, or 0.1s. I will use 0.1s.

    def _wait_for_rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    @staticmethod
    def extract_repo_id(url: Optional[str]) -> Optional[str]:
        """Extract canonical /owner/repo context7 identifier from a repository URL."""
        if not url:
            return None
        url = url.strip()
        m = re.search(r"(?:github\.com|gitlab\.com|codeberg\.org)[/:]([^/]+)/([^/#?]+)", url, re.IGNORECASE)
        if m:
            owner = m.group(1).lower()
            repo = m.group(2).lower()
            if repo.endswith(".git"):
                repo = repo[:-4]
            if repo in ("tree", "blob"):
                return None
            return f"/{owner}/{repo}"
        return None

    def search_libraries(self, gem_name: str) -> list:
        if not self.api_key:
            return []
        
        url = "https://context7.com/api/v2/libs/search"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {
            "libraryName": gem_name,
            "query": f"ruby gem {gem_name} documentation"
        }
        
        try:
            self._wait_for_rate_limit()
            r = requests.get(url, headers=headers, params=params, timeout=5)
            if r.status_code == 200:
                data = r.json()
                results = data if isinstance(data, list) else data.get("results", [])
                return results
        except Exception:
            pass
        return []

    def verify_library(self, gem_name: str, source_code_uri: Optional[str] = None, homepage_uri: Optional[str] = None) -> Optional[str]:
        """Resolve a library ID, ensuring it matches the source code repository path if available."""
        expected_id = self.extract_repo_id(source_code_uri) or self.extract_repo_id(homepage_uri)

        results = self.search_libraries(gem_name)
        if results and len(results) > 0:
            if expected_id:
                # Prioritize a result that matches the expected repository path
                for res in results:
                    lib_id = res.get("id") or res.get("libraryId") or ""
                    if lib_id.lower() == expected_id.lower():
                        return lib_id
                    if lib_id.lower().endswith(expected_id.split("/")[-1].lower()):
                        return lib_id
                # If API returned an unrelated library from another language, reject it and prefer expected_id
                first_id = results[0].get("id") or results[0].get("libraryId")
                if first_id and not first_id.lower().endswith(gem_name.lower().replace("_", "-")):
                    return expected_id
            return results[0].get("id") or results[0].get("libraryId") or expected_id

        return expected_id

    def query_context(self, lib_id: str, query: str) -> Optional[str]:
        if not self.api_key:
            return None
        
        url = "https://context7.com/api/v2/context"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {
            "libraryId": lib_id,
            "query": query
        }
        
        try:
            self._wait_for_rate_limit()
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        return None
