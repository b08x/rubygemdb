import re
from typing import List, Optional, Tuple

from rubygemdb.models.gem import GemEntry, GemClassification, GemSignals, GemRisks
from rubygemdb.models.categories import CATEGORIES, CATEGORY_BY_SLUG, VALID_CATEGORIES
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.embedding_scorer import EmbeddingScorer

_TOKEN_RE = re.compile(r"[^a-z0-9]+")

# Scoring weights: name hits dominate, dependency hits nudge, description
# term hits break ties for quiet-named gems.
_NAME_HIT_WEIGHT = 1.0
_NAME_HIT_CAP = 3
_DEP_HIT_WEIGHT = 0.4
_DEP_HIT_CAP = 2.0
_DESC_HIT_WEIGHT = 0.06
_DESC_HIT_CAP = 0.6
_EMBEDDING_WEIGHT = 1.0

# Fallback when nothing matches at all.
_DEFAULT_CATEGORY = "core_extensions"
_DEFAULT_CONFIDENCE = 0.4
_DEFAULT_RISKS = GemRisks(invasiveness=3, coupling=1, abstraction_leak="low")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.split(text.lower()) if t]


def _keyword_hits(tokens: list[str], text_lower: str, keyword: str) -> int:
    """A keyword hits when it equals a token, or (if it contains separators)
    appears as a substring of the raw text."""
    k = keyword.lower()
    if k in tokens:
        return 1
    if not k.isalnum() and k in text_lower:
        return 1
    return 0


class GemClassifier:
    def __init__(self, rubygems_service: RubyGemsService, llm_service: LLMService,
                 embedding_scorer: Optional[EmbeddingScorer] = None):
        self.rubygems = rubygems_service
        self.llm = llm_service
        self.embedding_scorer = embedding_scorer

    def _get_embedding_scorer(self) -> Optional[EmbeddingScorer]:
        if self.embedding_scorer is None:
            self.embedding_scorer = EmbeddingScorer()
        return self.embedding_scorer

    def heuristic_classify(self, name: str, info: dict,
                           category: Optional[str] = None) -> Tuple[GemClassification, GemSignals, list, List[str]]:
        description = str(info.get("info", "")) if info else ""
        deps = [d["name"] for d in info.get("dependencies", {}).get("runtime", [])] if info else []

        name_l = name.lower()
        name_tokens = _tokens(name)
        name_token_set = list(dict.fromkeys(name_tokens))
        dep_texts = []
        dep_tokens: list[str] = []
        for dep in deps:
            dep_l = dep.lower()
            dep_texts.append(dep_l)
            dep_tokens.extend(_tokens(dep))
        desc_l = description.lower()
        desc_tokens = _tokens(description)

        # ---- keyword + description scoring over all categories ----
        scores: dict[str, float] = {}
        name_hits: dict[str, int] = {}
        dep_hit_counts: dict[str, int] = {}

        for cat in CATEGORIES:
            n_hits = sum(_keyword_hits(name_token_set, name_l, kw) for kw in cat.keywords)
            d_hits = sum(_keyword_hits(dep_tokens, dep_l, kw) for kw in cat.keywords) if dep_tokens else 0
            t_hits = sum(_keyword_hits(desc_tokens, desc_l, kw) for kw in cat.keywords) if desc_tokens else 0

            name_hits[cat.slug] = n_hits
            dep_hit_counts[cat.slug] = d_hits
            scores[cat.slug] = (
                min(_NAME_HIT_WEIGHT * n_hits, _NAME_HIT_CAP)
                + min(_DEP_HIT_WEIGHT * d_hits, _DEP_HIT_CAP)
                + min(_DESC_HIT_WEIGHT * t_hits, _DESC_HIT_CAP)
            )

        # ---- embedding similarity (graceful degradation to empty dict) ----
        embedding_scores: dict[str, float] = {}
        try:
            scorer = self._get_embedding_scorer()
            if scorer is not None:
                embedding_scores = scorer.score(name, description) or {}
        except Exception:
            embedding_scores = {}

        if embedding_scores:
            best_emb = max(embedding_scores.values()) or 1.0
            for slug, sim in embedding_scores.items():
                if slug in scores:
                    scores[slug] += _EMBEDDING_WEIGHT * (sim / best_emb if best_emb else 0.0)

        best_slug = max(scores, key=lambda s: scores[s])  # type: ignore[arg-type,return-value]
        best_score = scores[best_slug]
        n_best = name_hits.get(best_slug, 0)
        d_best = dep_hit_counts.get(best_slug, 0)

        classification = GemClassification(primary=best_slug, confidence=_DEFAULT_CONFIDENCE)

        if best_score <= 0:
            classification.primary = _DEFAULT_CATEGORY
        elif n_best >= 1:
            classification.confidence = 0.8
        elif d_best >= 1:
            classification.confidence = 0.65
        else:
            # description-only or embedding-only match: too weak to trust
            classification.confidence = min(0.6, 0.5 + 0.1 * min(best_score, 1.0))

        # CSV group hint: development/test-scoped gems are testing tooling
        if category in ("development", "test"):
            classification.primary = "testing_qa"
            classification.confidence = max(classification.confidence, 0.7)

        signals = GemSignals()
        if info and info.get("platform") not in (None, "ruby"):
            signals.native_ext = True
        if n_best and CATEGORY_BY_SLUG[best_slug].layer == "plumbing":
            signals.external_io = True
        if any("rails" in d.lower() for d in deps):
            signals.rails = True

        # ---- dependency-driven sub-category signals (kept from the old
        # classifier; orthogonal to the 21-category taxonomy) ----
        sub_cats = set()
        for dep in deps:
            dl = dep.lower()
            if "rails" in dl:
                sub_cats.add("rails")
            if "sidekiq" in dl:
                sub_cats.add("sidekiq")
                sub_cats.add("background_jobs")
            if "active_job" in dl or "activejob" in dl:
                sub_cats.add("activejob")
            if "puma" in dl or "unicorn" in dl:
                sub_cats.add("server")
            if "redis" in dl:
                sub_cats.add("redis")
            if "postgres" in dl or dl == "pg" or "mysql" in dl or "mariadb" in dl:
                sub_cats.add("database")
            if "elasticsearch" in dl or "search" in dl:
                sub_cats.add("search")
            if "jwt" in dl or "oauth" in dl:
                sub_cats.add("auth")
            if "graphql" in dl or "grape" in dl:
                sub_cats.add("api")
            if "json" in dl or "xml" in dl:
                sub_cats.add("serialization")
            if "csv" in dl or "xlsx" in dl or "excel" in dl:
                sub_cats.add("spreadsheet")
            if "pdf" in dl:
                sub_cats.add("pdf")
            if "aws" in dl or "gcp" in dl or "google" in dl or "azure" in dl or "s3" in dl:
                sub_cats.add("cloud")
            if "sentry" in dl or "datadog" in dl or "newrelic" in dl or "honeybadger" in dl:
                sub_cats.add("monitoring")
            if "delayed_job" in dl or "resque" in dl or "sidekiq" in dl:
                sub_cats.add("background_jobs")
            if "kafka" in dl or "bunny" in dl or "mqtt" in dl:
                sub_cats.add("messaging")

        return classification, signals, deps, list(sub_cats)

    def score_gem(self, primary_cat: str, deps: list) -> GemRisks:
        cat = CATEGORY_BY_SLUG.get(primary_cat)
        if cat is None:
            # Unknown slug: fall back to default risk values without raising.
            return GemRisks(
                invasiveness=_DEFAULT_RISKS.invasiveness,
                coupling=min(4, max(1, len(deps) // 3 + 1)),
                abstraction_leak=_DEFAULT_RISKS.abstraction_leak,
            )

        coupling = min(4, max(1, len(deps) // 3 + 1))
        return GemRisks(
            invasiveness=cat.default_invasiveness,
            coupling=coupling,
            abstraction_leak=cat.default_abstraction_leak,
        )

    def classify(self, name: str, category: Optional[str] = None, homepage: Optional[str] = None,
                 source_code_uri: Optional[str] = None, context7_id: Optional[str] = None) -> GemEntry:
        info = self.rubygems.fetch_gem_info(name) or {}
        classification, signals, deps, sub_cats = self.heuristic_classify(name, info, category)

        classification.sub_categories = sub_cats

        # Unconditionally call the LLM to generate an agent-optimized description
        prompt = self.llm.build_prompt(name, info, deps)
        llm_result = self.llm.call_llm(prompt)
        agent_desc = ""

        if llm_result:
            # Only override heuristic classification if LLM is confident and heuristics weren't
            if classification.confidence < 0.7 and llm_result.get("confidence", 0) > 0.6:
                llm_primary = llm_result.get("primary")
                if llm_primary in VALID_CATEGORIES:
                    classification.primary = llm_primary
                    classification.confidence = llm_result["confidence"]
            agent_desc = llm_result.get("agent_description", "")

        risks = self.score_gem(classification.primary, deps)

        cat = CATEGORY_BY_SLUG.get(classification.primary)
        attaches_to = cat.slug.split("_")[0] if cat else classification.primary.split("_")[0]

        return GemEntry(
            name=name,
            classification=classification,
            role={
                "description": str(info.get("info", "")) if info else "",
                "agent_description": agent_desc,
                "attaches_to": attaches_to
            },
            risks=risks,
            signals=signals,
            dependencies=deps,
            description=info.get("info") if info else "",
            homepage=homepage,
            source_code_uri=source_code_uri,
            context7_id=context7_id
        )
