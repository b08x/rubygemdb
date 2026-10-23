import time
import requests
import json
from urllib.parse import quote_plus
from rubygemdb.core.config import settings

from typing import Optional

class RubyGemsService:
    def __init__(self):
        self.cache = self._load_cache()
        self._last_request_time = 0.0
        self.rate_limit_delay = 0.1 # seconds

    def _wait_for_rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _load_cache(self):
        if settings.gem_cache_file.exists():
            with open(settings.gem_cache_file, "r") as f:
                return json.load(f)
        return {}

    def _save_cache(self):
        with open(settings.gem_cache_file, "w") as f:
            json.dump(self.cache, f, indent=2)

    def fetch_gem_info(self, name: str, retries: int = 3) -> Optional[dict]:
        if name in self.cache:
            return self.cache[name]

        url = settings.rubygems_api_url.format(name=name)
        for attempt in range(retries):
            try:
                self._wait_for_rate_limit()
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    self.cache[name] = data
                    self._save_cache()
                    return data
                elif r.status_code == 404:
                    return None
            except Exception:
                time.sleep(1 * (attempt + 1))
        return None

    def search_gems(self, query: str, limit: int = 8) -> Optional[list[dict]]:
        """Incremental gem search against rubygems.org; used for find-as-you-type.

        Results are cached per query so repeated keystrokes don't re-hit the API.
        """
        query = query.strip()
        if not query:
            return []

        cache_key = f"__search__{query.lower()}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        url = settings.rubygems_search_url.format(query=quote_plus(query))
        try:
            self._wait_for_rate_limit()
            r = requests.get(url, timeout=5, params={"page": 1})
            if r.status_code != 200:
                return None
            data = r.json()
            if not isinstance(data, list):
                return None
            results = [
                {"name": g.get("name"), "info": g.get("info") or ""}
                for g in data
                if g.get("name")
            ][:limit]
            self.cache[cache_key] = results
            self._save_cache()
            return results
        except Exception:
            return None
