import re
from typing import List, Optional, Tuple

from rubygemdb.models.gem import GemEntry, GemClassification, GemSignals, GemRisks
from rubygemdb.models.categories import CATEGORIES, CATEGORY_BY_SLUG, VALID_CATEGORIES
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.embedding_scorer import EmbeddingScorer
from rubygemdb.services.tracing import trace_span

_TOKEN_RE = re.compile(r"[^a-z0-9]+")

# Scoring weights: name hits dominate, dependency hits nudge, description
# term hits break ties for quiet-named gems.
_NAME_HIT_WEIGHT = 1.0
_NAME_HIT_CAP = 4.0
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


def _name_keyword_score(tokens: list[str], name_lower: str, keyword: str) -> float:
    """Score a keyword match against a gem name. Exact full name match dominates,
    multi-token compound matches rank next, and single token hits follow."""
    k = keyword.lower()
    norm_k = k.replace("-", "_")
    norm_name = name_lower.replace("-", "_")
    if norm_k == norm_name:
        return 2.5
    if k in tokens:
        return 1.0
    if not k.isalnum() and (k in name_lower or norm_k in norm_name):
        return 1.5
    return 0.0


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
            n_score = sum(_name_keyword_score(name_token_set, name_l, kw) for kw in cat.keywords)
            d_hits = sum(_keyword_hits(dep_tokens, dep_l, kw) for kw in cat.keywords) if dep_tokens else 0
            t_hits = sum(_keyword_hits(desc_tokens, desc_l, kw) for kw in cat.keywords) if desc_tokens else 0

            name_hits[cat.slug] = int(n_score)
            dep_hit_counts[cat.slug] = d_hits
            scores[cat.slug] = (
                min(n_score, _NAME_HIT_CAP)
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

        # ---- taxonomy-driven sub-category detection ----
        sub_cats = self.detect_sub_categories(name, info, deps, classification.primary)
        classification.sub_categories = sub_cats

        return classification, signals, deps, sub_cats

    def detect_sub_categories(self, name: str, info: dict, deps: list, primary_cat: str) -> List[str]:
        cat = CATEGORY_BY_SLUG.get(primary_cat)
        if not cat or not cat.sub_categories:
            return []

        name_l = name.lower()
        name_tokens = set(_tokens(name))
        description = str(info.get("info", "")) if info else ""
        desc_l = description.lower()
        desc_tokens = set(_tokens(description))
        deps_l = [d.lower() for d in deps]

        matches: list[tuple[str, float]] = []
        for sub_slug, examples in cat.sub_categories.items():
            score = 0.0
            ex_lower = [e.lower() for e in examples]

            # 1. Direct name match in example gems
            if name_l in ex_lower:
                score += 10.0

            # 2. Substring / prefix match in example gems
            for ex in ex_lower:
                if ex in name_l:
                    score += 3.0
                    break

            # 3. Sub-slug token match against name and description
            sub_tokens = [t for t in sub_slug.split("_") if len(t) > 2 and t not in ("and", "for", "the")]
            name_sub_hits = sum(1 for st in sub_tokens if st in name_tokens or st in name_l)
            if name_sub_hits:
                score += 2.0 * name_sub_hits

            desc_sub_hits = sum(1 for st in sub_tokens if st in desc_tokens or st in desc_l)
            if desc_sub_hits:
                score += 0.5 * desc_sub_hits

            # 4. Dependency match against example gems
            dep_example_hits = sum(1 for ex in ex_lower if ex in deps_l or any(ex in d for d in deps_l))
            if dep_example_hits:
                score += 2.5 * dep_example_hits

            if score >= 2.0:
                matches.append((sub_slug, score))

        if not matches:
            # Fallback: if no score >= 2.0, check description tokens
            best_sub = None
            best_sc = 0
            for sub_slug, _ in cat.sub_categories.items():
                sub_tokens = [t for t in sub_slug.split("_") if len(t) > 2]
                sc = sum(1 for st in sub_tokens if st in desc_tokens)
                if sc > best_sc:
                    best_sc = sc
                    best_sub = sub_slug
            if best_sub and best_sc > 0:
                matches.append((best_sub, float(best_sc)))

        matches.sort(key=lambda m: m[1], reverse=True)
        return [m[0] for m in matches]

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
        with trace_span(
            name="classify-gem",
            input={"name": name, "category_hint": category},
            metadata={
                "homepage": homepage,
                "source_code_uri": source_code_uri,
                "context7_id": context7_id,
            },
            tags=["rubygemdb", "classifier"],
        ) as span:
            info = self.rubygems.fetch_gem_info(name) or {}

            with trace_span(
                name="heuristic-classify",
                input={"name": name, "category_hint": category},
            ) as h_span:
                classification, signals, deps, sub_cats = self.heuristic_classify(name, info, category)
                classification.sub_categories = sub_cats
                h_span.update(output={
                    "primary": classification.primary,
                    "confidence": classification.confidence,
                    "sub_categories": sub_cats,
                })

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
                        with trace_span(
                            name="detect-subcategories",
                            input={"primary": llm_primary, "deps_count": len(deps)},
                        ) as sc_span:
                            sub_cats = self.detect_sub_categories(name, info, deps, llm_primary)
                            classification.sub_categories = sub_cats
                            sc_span.update(output={"sub_categories": sub_cats})

                agent_desc = llm_result.get("agent_description", "")

            risks = self.score_gem(classification.primary, deps)

            cat = CATEGORY_BY_SLUG.get(classification.primary)
            attaches_to = cat.slug.split("_")[0] if cat else classification.primary.split("_")[0]

            span.update(output={
                "name": name,
                "primary": classification.primary,
                "confidence": classification.confidence,
                "sub_categories": classification.sub_categories,
            })

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
