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
                if data and isinstance(data, list) and len(data) > 0:
                    # Return the libraryId of the first match if it looks relevant
                    # For now, just return the first one's libraryId
                    return data[0].get("libraryId")
        except Exception:
            pass
        return None
