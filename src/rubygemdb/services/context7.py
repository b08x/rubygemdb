import requests
from typing import Optional
from rubygemdb.core.config import settings

class Context7Service:
    def __init__(self):
        self.api_key = settings.context7_api_key

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
            r = requests.get(url, headers=headers, params=params, timeout=5)
            if r.status_code == 200:
                data = r.json()
                results = data if isinstance(data, list) else data.get("results", [])
                return results
        except Exception:
            pass
        return []

    def verify_library(self, gem_name: str) -> Optional[str]:
        results = self.search_libraries(gem_name)
        if results and len(results) > 0:
            # Return the id or libraryId of the first match
            return results[0].get("id") or results[0].get("libraryId")
        return None

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
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        return None
