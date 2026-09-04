import time
import requests
import json
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
