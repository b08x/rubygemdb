from typing import Dict, List, Tuple, Any
from rubygemdb.models.gem import GemEntry, GemClassification, GemSignals, GemRisks
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService

class GemClassifier:
    def __init__(self, rubygems_service: RubyGemsService, llm_service: LLMService):
        self.rubygems = rubygems_service
        self.llm = llm_service

    def heuristic_classify(self, name: str, info: dict, category: str = None) -> Tuple[GemClassification, GemSignals, list]:
        lname = name.lower()
        def has(keys):
            return any(k in lname for k in keys)

        classification = GemClassification(primary="application_capability", confidence=0.6)
        signals = GemSignals()
        deps = [d["name"] for d in info.get("dependencies", {}).get("runtime", [])] if info else []

        if has(["active_support", "core_ext", "dry-"]):
            classification.primary = "runtime_substrate"
            classification.confidence += 0.2
        elif "railties" in deps or has(["rails", "engine", "sidekiq"]):
            classification.primary = "framework_integration"
            signals.rails = True
            classification.confidence += 0.3
        elif has(["http", "faraday", "aws", "google", "stripe", "grpc"]):
            classification.primary = "boundary_interface"
            signals.external_io = True
            classification.confidence += 0.2
        elif has(["pundit", "auth", "jwt"]):
            classification.primary = "policy_enforcement"
            classification.confidence += 0.2
        elif has(["sentry", "datadog", "newrelic", "log"]):
            classification.primary = "observability"
            signals.external_io = True
            classification.confidence += 0.2
        elif category in ["development", "test"] or has(["rspec", "rubocop", "pry", "tty-"]):
            classification.primary = "developer_experience"
            classification.confidence += 0.3

        if info and info.get("platform") not in (None, "ruby"):
            signals.native_ext = True

        return classification, signals, deps

    def score_gem(self, primary_cat: str, deps: list) -> GemRisks:
        inv = 3
        if primary_cat == "runtime_substrate":
            inv = 5
        elif primary_cat == "framework_integration":
            inv = 4
        elif primary_cat == "boundary_interface":
            inv = 2
        elif primary_cat == "developer_experience":
            inv = 1

        coupling = min(4, max(1, len(deps)//3 + 1))
        leak = "high" if primary_cat == "boundary_interface" else (
            "medium" if primary_cat == "framework_integration" else "low"
        )

        return GemRisks(invasiveness=inv, coupling=coupling, abstraction_leak=leak)

    def classify(self, name: str, category: str = None, homepage: str = None, source_code_uri: str = None, context7_id: str = None) -> GemEntry:
        info = self.rubygems.fetch_gem_info(name)
        classification, signals, deps = self.heuristic_classify(name, info, category)

        if classification.confidence < 0.7:
            prompt = self.llm.build_prompt(name, info, deps)
            llm_result = self.llm.call_llm(prompt)
            if llm_result and llm_result.get("confidence", 0) > 0.6:
                classification.primary = llm_result["primary"]
                classification.confidence = llm_result["confidence"]

        risks = self.score_gem(classification.primary, deps)

        return GemEntry(
            name=name,
            classification=classification,
            role={
                "description": info.get("info") if info else "",
                "attaches_to": classification.primary.split("_")[0]
            },
            risks=risks,
            signals=signals,
            dependencies=deps,
            description=info.get("info") if info else "",
            homepage=homepage,
            source_code_uri=source_code_uri,
            context7_id=context7_id
        )
