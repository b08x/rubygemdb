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

    def infer_genai_uses(
        self,
        name: str,
        description: str,
        category: str,
        sub_categories: Optional[list[str]] = None,
    ) -> Optional[str]:
        """Infer GenAI backend tooling use case via LLM, with persistent JSON caching."""
        api_key = settings.llm_api_key
        if not api_key:
            return None

        sub_cat_str = ", ".join(sub_categories) if sub_categories else "none"
        prompt = f"""
Infer the primary role and use case of this Ruby gem for GenAI backend tooling (such as AI agent memory, prompt tooling, tool calling / execution harness, RAG ETL / document preprocessing, structured output validation, or agent evaluation).

Gem: {name}
Category: {category} (sub-categories: {sub_cat_str})
Description: {description}

Return JSON only:
{{"genai_uses": "Concise 1-2 sentence summary of its role in GenAI backend tooling"}}
"""
        key = self._prompt_hash(prompt)
        if key in self.cache:
            val = self.cache[key]
            if isinstance(val, dict):
                return val.get("genai_uses")
            if isinstance(val, str):
                return val

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": settings.active_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }

        with trace_generation(
            name="infer-genai-uses-llm",
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
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0].strip()
                    elif "```" in text:
                        text = text.split("```")[1].split("```")[0].strip()

                    result = json.loads(text)
                    self.cache[key] = result
                    self._save_cache()

                    gen.update(output=result)
                    if isinstance(result, dict):
                        return result.get("genai_uses")
                    return str(result)
                else:
                    gen.update(
                        level="ERROR",
                        status_message=f"HTTP {r.status_code}: {r.text[:200]}",
                    )
            except Exception as e:
                gen.update(level="ERROR", status_message=str(e))

        return None

    def refine_stack_selection(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        max_gems: int = 10,
    ) -> Optional[list[dict[str, str]]]:
        """Curate candidate pool down to a cohesive, compact stack (5-12 gems) and assign task-specific roles.

        Returns a list of dicts with keys 'gem' and 'role' if successful, or None on failure/offline.
        """
        api_key = settings.llm_api_key
        if not api_key or not candidates:
            return None

        # Build formatted list of candidates and set of candidate names
        cand_lines = []
        candidate_names = set()
        for c in candidates:
            name = c.get("gem") or c.get("name")
            if not name:
                continue
            candidate_names.add(name.lower())
            cat = c.get("category", "unknown")
            layer = c.get("layer", "unknown")
            desc = c.get("description") or c.get("role", "")
            cand_lines.append(f"- {name} (category: {cat}, layer: {layer}): {desc}")

        cand_text = "\n".join(cand_lines)
        prompt = f"""You are an expert Ruby system architect. Given the following user workflow query and candidate gems, select a cohesive, compact Ruby gem stack of 5 to {max_gems} gems tailored specifically to the workflow.

User Workflow: "{query}"

Candidate Gems:
{cand_text}

Rules:
1. Select between 5 and {max_gems} gems total that together form a complete, minimal, and non-redundant stack for this workflow. If fewer than 5 candidates exist, select all relevant ones.
2. Limit each category to at most 1–2 gems (maximum 3 only if complementary).
3. Only select gems from the candidate pool list above. Do NOT invent new gems.
4. For each selected gem, write a task-specific, contextual role description explaining its exact architectural purpose for this specific workflow (e.g. "Terminal UI rendering & key handling", "Fast regex & token segmentation").
5. Omit irrelevant gems, redundant alternatives, and unused categories.

Return JSON only:
{{
  "selected_gems": [
    {{"gem": "gem_name", "role": "exact architectural purpose for this workflow"}}
  ]
}}
"""
        key = self._prompt_hash(prompt)
        if key in self.cache:
            val = self.cache[key]
            with trace_generation(
                name="refine-stack-selection-llm",
                model=settings.active_model,
                input={"messages": [{"role": "user", "content": prompt}]},
                output=val,
                metadata={"cache_hit": True, "provider": settings.active_provider},
            ):
                return self._parse_stack_selection(val, candidate_names, max_gems)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": settings.active_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }

        with trace_generation(
            name="refine-stack-selection-llm",
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
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0].strip()
                    elif "```" in text:
                        text = text.split("```")[1].split("```")[0].strip()

                    result = json.loads(text)
                    self.cache[key] = result
                    self._save_cache()

                    gen.update(output=result)
                    return self._parse_stack_selection(result, candidate_names, max_gems)
                else:
                    gen.update(level="ERROR", status_message=f"HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                gen.update(level="ERROR", status_message=str(e))

        return None

    def _parse_stack_selection(
        self,
        result: Any,
        candidate_names: set[str],
        max_gems: int,
    ) -> Optional[list[dict[str, str]]]:
        if not isinstance(result, dict) or "selected_gems" not in result:
            return None
        raw_list = result.get("selected_gems")
        if not isinstance(raw_list, list) or not raw_list:
            return None

        curated = []
        seen = set()
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            gem_name = str(item.get("gem", "")).strip()
            role = str(item.get("role", "")).strip()
            if not gem_name or not role:
                continue
            if gem_name.lower() not in candidate_names:
                continue
            if gem_name.lower() in seen:
                continue
            seen.add(gem_name.lower())
            curated.append({"gem": gem_name, "role": role})
            if len(curated) >= max_gems:
                break

        return curated if curated else None
