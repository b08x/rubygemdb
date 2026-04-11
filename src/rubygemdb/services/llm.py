import json
import requests
from hashlib import sha256
from rubygemdb.core.config import settings

class LLMService:
    def __init__(self):
        self.cache = self._load_cache()

    def _load_cache(self):
        if settings.llm_cache_file.exists():
            with open(settings.llm_cache_file, "r") as f:
                return json.load(f)
        return {}

    def _save_cache(self):
        with open(settings.llm_cache_file, "w") as f:
            json.dump(self.cache, f, indent=2)

    def _prompt_hash(self, prompt: str) -> str:
        return sha256(prompt.encode()).hexdigest()

    def build_prompt(self, name: str, info: dict, deps: list) -> str:
        description = info.get('info', '') if info else ''
        categories = [
            "runtime_spine",
            "cli_terminal_ui",
            "storage_persistence",
            "async_networking_orchestration",
            "ai_nlp",
            "data_processing",
            "retrieval_similarity_fuzzy",
            "algorithms_knowledge_structures",
            "validation_types",
            "parsing_encoding",
            "debugging_introspection",
            "mcp_tooling",
        ]
        cat_list = "\n".join(f"- {c}" for c in categories)
        return f"""
Classify this Ruby gem into ONE category:
{cat_list}

Gem:
{name}
Description:
{description}
Dependencies:
{', '.join(deps)}

Return JSON only:
{{"primary":"...","confidence":0.0}}
"""

    def call_llm(self, prompt: str) -> dict:
        if not settings.devstral_api_key:
            return None
            
        key = self._prompt_hash(prompt)
        if key in self.cache:
            return self.cache[key]

        headers = {"Authorization": f"Bearer {settings.devstral_api_key}"}
        payload = {
            "model": settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0
        }

        try:
            r = requests.post(settings.llm_endpoint, json=payload, headers=headers, timeout=10)
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"]
                # Handle possible markdown backticks in LLM response
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                
                result = json.loads(text)
                self.cache[key] = result
                self._save_cache()
                return result
        except Exception:
            pass

        return None
