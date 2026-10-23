import json
import requests
from typing import Optional, Any
from hashlib import sha256
from rubygemdb.core.config import settings
from rubygemdb.services.tracing import trace_generation

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
        from rubygemdb.models.categories import CATEGORIES
        cat_list = "\n".join(f"- {c.slug} ({c.layer}): {c.description}" for c in CATEGORIES)
        valid = ", ".join(c.slug for c in CATEGORIES)
        return f"""
Classify this Ruby gem into ONE category, and write an agent-optimized description.

Categories:
{cat_list}

Gem:
{name}
Description:
{description}
Dependencies:
{', '.join(deps)}

The 'agent_description' must be a concise, RAG-optimized summary for another AI coding agent. Explain exactly what this gem does, when to use it, and how it might combine with other tools.

The "primary" value MUST be exactly one of these slugs: {valid}

Return JSON only:
{{"primary":"...","confidence":0.0, "agent_description": "..."}}
"""

    def call_llm(self, prompt: str) -> Optional[dict]:
        api_key = settings.llm_api_key
        if not api_key:
            return None
            
        key = self._prompt_hash(prompt)
        if key in self.cache:
            with trace_generation(
                name="classify-gem-llm",
                model=settings.active_model,
                input={"messages": [{"role": "user", "content": prompt}]},
                output=self.cache[key],
                metadata={"cache_hit": True, "provider": settings.active_provider},
            ):
                return self.cache[key]

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": settings.active_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0
        }

        with trace_generation(
            name="classify-gem-llm",
            model=settings.active_model,
            input={"messages": payload["messages"]},
            model_parameters={"temperature": 0},
            metadata={
                "cache_hit": False,
                "provider": settings.active_provider,
                "endpoint": settings.active_llm_endpoint,
            },
        ) as gen:
            try:
                r = requests.post(settings.active_llm_endpoint, json=payload, headers=headers, timeout=15)
                if r.status_code == 200:
                    resp_data = r.json()
                    text = resp_data["choices"][0]["message"]["content"]
                    # Handle possible markdown backticks in LLM response
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0].strip()
                    elif "```" in text:
                        text = text.split("```")[1].split("```")[0].strip()
                    
                    result = json.loads(text)
                    self.cache[key] = result
                    self._save_cache()

                    # Track token usage if provided by provider
                    usage = resp_data.get("usage")
                    usage_details = None
                    if isinstance(usage, dict):
                        usage_details = {
                            "input": int(usage.get("prompt_tokens", 0)),
                            "output": int(usage.get("completion_tokens", 0)),
                            "total": int(usage.get("total_tokens", 0)),
                        }

                    gen.update(output=result, usage_details=usage_details)
                    return result
                else:
                    gen.update(
                        level="ERROR",
                        status_message=f"HTTP {r.status_code}: {r.text[:200]}",
                    )
            except Exception as e:
                gen.update(level="ERROR", status_message=str(e))

        return None
