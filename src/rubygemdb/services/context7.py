import requests
from typing import Optional
from rubygemdb.core.config import settings

class Context7Service:
    def __init__(self):
        self.api_key = settings.context7_api_key

    def verify_library(self, gem_name: str) -> Optional[str]:
        if not self.api_key:
            return None
        
        search_url = f"https://context7.com/api/v2/libs/search?libraryName={gem_name}&query=ruby+gem+{gem_name}+documentation"
        headers = {"x-api-key": self.api_key}
        
        try:
            r = requests.get(search_url, headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                results = data if isinstance(data, list) else data.get("results", [])
                if results and len(results) > 0:
                    # Return the libraryId or id of the first match
                    return results[0].get("libraryId") or results[0].get("id")
        except Exception:
            pass
        return None

    def query_context(self, lib_id: str, query: str) -> Optional[str]:
        if not self.api_key:
            return None
        
        import urllib.parse
        encoded_query = urllib.parse.quote(query)
        url = f"https://context7.com/api/v2/context?libraryId={lib_id}&query={encoded_query}"
        headers = {"x-api-key": self.api_key}
        
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        return None
