import time
import requests
import json
from rubygemdb.core.config import settings

class RubyGemsService:
    def __init__(self):
        self.cache = self._load_cache()

    def _load_cache(self):
        if settings.gem_cache_file.exists():
            with open(settings.gem_cache_file, "r") as f:
                return json.load(f)
        return {}

    def _save_cache(self):
        with open(settings.gem_cache_file, "w") as f:
            json.dump(self.cache, f, indent=2)

    def fetch_gem_info(self, name: str, retries: int = 3) -> dict:
        if name in self.cache:
            return self.cache[name]

        url = settings.rubygems_api_url.format(name=name)
        for attempt in range(retries):
            try:
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    self.cache[name] = data
                    self._save_cache()
                    return data
            except Exception:
                time.sleep(1 * (attempt + 1))
        return None
