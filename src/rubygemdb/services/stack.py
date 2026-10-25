"""Layered gem-stack composition for back-end tooling design.

Turns a context query into a layered stack manifest (substrate -> quality)
plus a Gemfile snippet via a two-stage hierarchical retrieval and refinement pipeline:
1. Relevance-Gated Candidate Pool: semantic retrieval from txtai + relevance-gated
   seeds and preferred picks matching active categories only.
2. Second-Pass Curation: curates candidate pool to a compact, cohesive stack (5-12 gems)
   with category density limits (max 1-3 gems per category) and contextual task-specific
   role descriptions (via LLM synthesis when available or local scoring offline).
"""

import re
from typing import Dict, List, Optional, Set

import yaml  # type: ignore

from rubygemdb.core.config import settings
from rubygemdb.models.categories import CATEGORIES, CATEGORY_BY_SLUG, LAYERS
from rubygemdb.services.llm import LLMService

_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_GEM_NAME_RE = re.compile(r"^Gem Name: (\S+)")

KNOWN_SEED_ROLES: Dict[str, str] = {
    "dotenv": "Environment variable configuration and secrets loader",
    "drydock": "CLI framework, command dispatch, and option parsing",
    "thor": "Command-line suite, subcommands, and option parsing",
    "gli": "Command-line interface structure and switch parsing",
    "highline": "Terminal user interaction, prompting, and color formatting",
    "tty-prompt": "Interactive terminal prompts and selection menus",
    "tty-table": "Terminal table formatting and tabular display",
    "tty-progressbar": "Terminal progress bars and task status indicators",
    "tty-screen": "Terminal screen sizing and terminal capability detection",
    "pastel": "Terminal text styling, colorizing, and ANSI escaping",
    "pry": "Interactive runtime debugging and inspection REPL",
    "rubocop": "Static code analysis, style linting, and quality enforcement",
    "journald-logger": "Structured system logging and systemd journal integration",
    "rspec": "Behavior-driven testing, specification, and mock suite",
    "minitest": "Lightweight unit testing, assertions, and benchmarking",
    "nokogiri": "HTML/XML document parsing, scraping, and XPath traversal",
    "sequel": "Database connectivity, query building, and schema migrations",
    "activerecord": "Object-relational mapping and database abstraction",
    "pg": "PostgreSQL native database adapter and query execution",
    "sqlite3": "SQLite embedded database engine and storage driver",
    "redis": "In-memory key-value data store and cache client",
    "concurrent-ruby": "Concurrency abstractions, thread pools, and async futures",
    "async": "Asynchronous event-driven I/O and fiber-based concurrency",
    "falcon": "High-performance asynchronous Rack web and application server",
    "puma": "Multi-threaded HTTP application server",
    "rack": "Rack interface adapter and HTTP request routing middleware",
    "faraday": "Modular HTTP client and external API connection adapter",
    "httparty": "HTTP request convenience client and response handler",
    "pragmatic_segmenter": "Rule-based sentence boundary disambiguation and segmentation",
    "amatch": "Approximate string matching and fuzzy text distance algorithms",
    "ruby_llm": "Unified multi-provider LLM chat, completion, and tool calling",
    "ruby_llm-mcp": "Model Context Protocol client for AI tool integration",
    "dry-schema": "Strict data structure validation and predicate assertions",
    "dry-types": "Flexible type system and data coercion constraints",
    "bcrypt_pbkdf": "Cryptographic password hashing and authentication security",
    "devise": "Authentication and user session management solution",
    "pundit": "Object-oriented authorization and policy enforcement",
    "ffi": "Foreign function interface for native C library bindings",
    "childprocess": "Cross-platform subprocess spawning and lifecycle management",
    "sidekiq": "Redis-backed background job processing and asynchronous queues",
    "glimmer-dsl-libui": "Cross-platform native desktop GUI windowing and controls",
    "asciidoctor": "AsciiDoc text processing and documentation generation",
    "rouge": "Syntax highlighting and code token rendering",
    "ruby-vips": "High-throughput image processing and pixel manipulation",
    "oga": "Lightweight XML and HTML document parser",
}


def guess_category(name: str) -> str:
    """Cheap keyword lookup for gems whose category metadata is unavailable."""
    tokens = set(t for t in _TOKEN_RE.split(name.lower()) if t)
    name_l = name.lower()
    best, best_hits = "core_extensions", 0
    for cat in CATEGORIES:
        hits = 0
        for kw in cat.keywords:
            k = kw.lower()
            if k in tokens or (not k.isalnum() and k in name_l):
                hits += 1
        if hits > best_hits:
            best, best_hits = cat.slug, hits
    return best


def seed_gems() -> List[dict]:
    """All curated base picks, ordered layer-first."""
    seeds = []
    for cat in CATEGORIES:
        for gem in cat.base_gems:
            seeds.append({"gem": gem, "category": cat.slug, "layer": cat.layer})
    seeds.sort(key=lambda s: LAYERS.index(s["layer"]))
    return seeds


class StackService:
    def __init__(
        self,
        index=None,
        storage=None,
        limit: int = 30,
        read_only: bool = False,
        llm_service: Optional[LLMService] = None,
    ):
        """index: txtai Embeddings (or a compatible mock with .search).
        storage: SQLiteStorage (or compatible mock) for seed inventory adds.
        limit: candidate retrieval pool limit.
        read_only: whether database mutations are disallowed.
        llm_service: optional LLMService instance for second-pass curation.
        """
        self._index = index
        self.storage = storage
        self.limit = limit
        self.read_only = read_only
        self.llm_service = llm_service

    def _load_index(self):
        from txtai import Embeddings  # type: ignore

        index_path = settings.txtai_dir / "rubygems_index"
        if not index_path.exists():
            return None
        try:
            embeddings = Embeddings(path=settings.embedding_model_path, backend="sqlite", content=True, gpu=settings.txtai_gpu)
            embeddings.load(str(index_path))
            return embeddings
        except Exception:
            return None

    @property
    def index(self):
        if self._index is not None:
            return self._index
        self._index = self._load_index()
        return self._index

    def _retrieve(self, query: str, limit: Optional[int] = None) -> List[dict]:
        index = self.index
        if index is None:
            return []
        n_limit = limit if limit is not None else self.limit
        results = index.search(query, n_limit)
        matches = []
        for r in results:
            if isinstance(r, (tuple, list)) and len(r) == 2:
                r = {"id": r[0], "score": r[1]}
            if not isinstance(r, dict):
                continue
            # Only gem documents are stack candidates; the index also holds
            # chat history and context7 cheatsheet chunks. Gem docs are
            # reliably identified by the "Gem Name: <x>" text prefix written
            # by the indexer (plain search() does not return the type column).
            name = None
            text = str(r.get("text") or "")
            m = _GEM_NAME_RE.match(text)
            if m:
                name = m.group(1)
            elif r.get("type") in (None, "gem") and r.get("name"):
                name = str(r["name"])
            if not name:
                continue
            category = r.get("category") or guess_category(name)
            matches.append({
                "gem": name,
                "category": category,
                "layer": CATEGORY_BY_SLUG[category].layer if category in CATEGORY_BY_SLUG else "substrate",
                "score": float(r.get("score", 0.0)),
            })
        return matches

    def _lookup_gem_description(self, gem_name: str) -> Optional[str]:
        """Look up description from storage or return None."""
        if self.storage is not None:
            try:
                if hasattr(self.storage, "get_classified_gem"):
                    cls_gem = self.storage.get_classified_gem(gem_name)
                    if cls_gem:
                        if cls_gem.role and cls_gem.role.get("agent_description"):
                            return cls_gem.role["agent_description"]
                        if cls_gem.description:
                            return cls_gem.description
                if hasattr(self.storage, "get_inventory_gem"):
                    inv_gem = self.storage.get_inventory_gem(gem_name)
                    if inv_gem and inv_gem.get("description"):
                        return inv_gem["description"]
            except Exception:
                pass
        return None

    def _identify_relevant_categories(self, query: str, matches: List[dict]) -> Set[str]:
        """Identify which taxonomy categories are relevant to the query based on search matches and lexical cues."""
        relevant: Set[str] = set()

        # 1. Categories from top search matches
        for m in matches:
            cat = m.get("category")
            if cat and cat in CATEGORY_BY_SLUG:
                relevant.add(cat)

        # 2. Categories from lexical token matches
        tokens = set(t for t in _TOKEN_RE.split(query.lower()) if t)
        query_l = query.lower()

        for cat in CATEGORIES:
            # Match slug or slug tokens (e.g. 'cli', 'libraries')
            slug_tokens = set(cat.slug.split("_"))
            if slug_tokens.intersection(tokens):
                relevant.add(cat.slug)
                continue

            # Match label tokens
            label_tokens = set(t for t in _TOKEN_RE.split(cat.label.lower()) if t)
            if label_tokens.intersection(tokens):
                relevant.add(cat.slug)
                continue

            # Match keywords
            kw_match = False
            for kw in cat.keywords:
                k = kw.lower()
                if k in tokens or (len(k) > 3 and k in query_l):
                    relevant.add(cat.slug)
                    kw_match = True
                    break
            if kw_match:
                continue

            # Match sub-categories
            for sub_slug, examples in cat.sub_categories.items():
                sub_tokens = set(sub_slug.split("_"))
                if sub_tokens.intersection(tokens):
                    relevant.add(cat.slug)
                    break
                if any(eg.lower() in tokens or (len(eg) > 3 and eg.lower() in query_l) for eg in examples):
                    relevant.add(cat.slug)
                    break

        # Fallback baseline when no categories match (e.g. no index and query has no category keywords)
        if not relevant:
            relevant = {"core_extensions", "developer_tools", "code_quality_typing"}

        return relevant

    def _gather_candidate_pool(
        self,
        query: str,
        relevant_categories: Set[str],
        matches: List[dict],
    ) -> List[dict]:
        """Assemble candidate pool restricted to relevant categories."""
        # Ensure curated base seeds exist in inventory so they stay retrievable
        if self.storage is not None and not self.read_only:
            all_seeds = seed_gems()
            for seed in all_seeds:
                try:
                    self.storage.add_gem_to_inventory(seed["gem"])
                except Exception:
                    pass

        pool_by_name: Dict[str, dict] = {}

        # 1. Add search matches belonging to relevant categories
        for m in matches:
            cat = m.get("category") or guess_category(m["gem"])
            if cat not in relevant_categories:
                continue
            gem_name = m["gem"]
            layer = m.get("layer") or (CATEGORY_BY_SLUG[cat].layer if cat in CATEGORY_BY_SLUG else "substrate")
            desc = self._lookup_gem_description(gem_name)
            pool_by_name[gem_name.lower()] = {
                "gem": gem_name,
                "category": cat,
                "layer": layer,
                "score": float(m.get("score", 0.5)),
                "source": "match",
                "description": desc,
            }

        # 2. Add curated base seeds for relevant categories
        for cat_slug in relevant_categories:
            cat = CATEGORY_BY_SLUG.get(cat_slug)
            if not cat:
                continue
            for gem_name in cat.base_gems:
                key = gem_name.lower()
                desc = self._lookup_gem_description(gem_name)
                if key in pool_by_name:
                    pool_by_name[key]["score"] = max(pool_by_name[key]["score"], 0.8)
                    pool_by_name[key]["source"] = "seed"
                    if desc and not pool_by_name[key].get("description"):
                        pool_by_name[key]["description"] = desc
                else:
                    pool_by_name[key] = {
                        "gem": gem_name,
                        "category": cat_slug,
                        "layer": cat.layer,
                        "score": 0.8,
                        "source": "seed",
                        "description": desc,
                    }

        # 3. Add user preferred gems for relevant categories
        if self.storage is not None:
            try:
                preferred = self.storage.get_preferred()
            except Exception:
                preferred = []
            for pref in preferred:
                gem_name = pref["name"]
                cat = pref.get("category") or guess_category(gem_name)
                if cat not in relevant_categories:
                    continue
                key = gem_name.lower()
                layer = pref.get("layer") or (CATEGORY_BY_SLUG[cat].layer if cat in CATEGORY_BY_SLUG else "substrate")
                desc = self._lookup_gem_description(gem_name) or pref.get("description")
                if key in pool_by_name:
                    pool_by_name[key]["score"] = max(pool_by_name[key]["score"], 0.85)
                    pool_by_name[key]["source"] = "preferred"
                    if desc:
                        pool_by_name[key]["description"] = desc
                else:
                    pool_by_name[key] = {
                        "gem": gem_name,
                        "category": cat,
                        "layer": layer,
                        "score": 0.85,
                        "source": "preferred",
                        "description": desc,
                    }

        return list(pool_by_name.values())

    def _generate_contextual_role(
        self,
        gem: str,
        category: str,
        query: str,
        source: str = "match",
        description: Optional[str] = None,
    ) -> str:
        """Generate a contextual task-specific role description explaining architectural purpose."""
        # 1. Known seed role
        if gem.lower() in KNOWN_SEED_ROLES:
            role_text = KNOWN_SEED_ROLES[gem.lower()]
            if source == "preferred":
                return f"user preferred pick - {role_text}"
            return role_text

        # 2. From description if available
        if description:
            clean = description.strip().replace("\n", " ")
            first_sent = clean.split(". ")[0].rstrip(".")
            if len(first_sent) > 80:
                first_sent = first_sent[:77] + "..."
            if first_sent:
                if source == "preferred":
                    return f"user preferred pick - {first_sent}"
                return first_sent

        # 3. From category label + query context
        cat_obj = CATEGORY_BY_SLUG.get(category)
        cat_label = cat_obj.label if cat_obj else category
        if source == "preferred":
            return f"user preferred pick - {cat_label} component"
        return f"{cat_label} component for {query}"

    def _offline_refine_candidates(
        self,
        query: str,
        candidates: List[dict],
        max_gems: int = 10,
    ) -> List[dict]:
        """Second-pass offline refinement: score thresholding, category density caps, and contextual role assignment."""
        if not candidates:
            return []

        tokens = set(t for t in _TOKEN_RE.split(query.lower()) if t)

        # 1. Compute effective score
        scored: List[dict] = []
        for c in candidates:
            base_score = float(c.get("score", 0.5))
            boost = 0.0
            if c.get("source") == "preferred":
                boost += 0.20
            elif c.get("source") == "seed":
                boost += 0.10

            name_tokens = set(t for t in _TOKEN_RE.split(c["gem"].lower()) if t)
            if name_tokens.intersection(tokens):
                boost += 0.15

            eff_score = base_score + boost
            scored.append({**c, "effective_score": eff_score})

        # Sort by effective score descending
        scored.sort(key=lambda x: x["effective_score"], reverse=True)

        # Group by category
        by_category: Dict[str, List[dict]] = {}
        for c in scored:
            by_category.setdefault(c["category"], []).append(c)

        # Category density limiting:
        sorted_categories = sorted(
            by_category.keys(),
            key=lambda cat: max(x["effective_score"] for x in by_category[cat]),
            reverse=True,
        )

        selected: List[dict] = []
        selected_gems_set: Set[str] = set()

        # Pass 1: 1 gem per category
        for cat in sorted_categories:
            if len(selected) >= max_gems:
                break
            gem_entry = by_category[cat][0]
            selected.append(gem_entry)
            selected_gems_set.add(gem_entry["gem"].lower())

        # Pass 2: If under max_gems, allow 2nd gem in categories with high scoring candidates
        if len(selected) < max_gems:
            for cat in sorted_categories:
                if len(selected) >= max_gems:
                    break
                if len(by_category[cat]) > 1:
                    second_gem = by_category[cat][1]
                    if second_gem["gem"].lower() not in selected_gems_set:
                        selected.append(second_gem)
                        selected_gems_set.add(second_gem["gem"].lower())

        # Pass 3: If still under min(5, len(candidates), max_gems), allow 3rd gem from top categories
        target_min = min(5, len(candidates), max_gems)
        if len(selected) < target_min:
            for cat in sorted_categories:
                if len(selected) >= max_gems or len(selected) >= target_min:
                    break
                if len(by_category[cat]) > 2:
                    third_gem = by_category[cat][2]
                    if third_gem["gem"].lower() not in selected_gems_set:
                        selected.append(third_gem)
                        selected_gems_set.add(third_gem["gem"].lower())

        # Assign contextual task-specific roles
        curated: List[dict] = []
        for entry in selected:
            role = self._generate_contextual_role(
                gem=entry["gem"],
                category=entry["category"],
                query=query,
                source=entry.get("source", "match"),
                description=entry.get("description"),
            )
            curated.append({
                "gem": entry["gem"],
                "category": entry["category"],
                "layer": entry["layer"],
                "role": role,
                "source": entry.get("source", "match"),
            })

        return curated

    def _apply_llm_selection(
        self,
        llm_picks: List[dict],
        candidates: List[dict],
        max_gems: int = 10,
    ) -> List[dict]:
        """Map LLM selection results back to candidate records with category density limits."""
        cand_map = {c["gem"].lower(): c for c in candidates}
        selected: List[dict] = []
        cat_counts: Dict[str, int] = {}

        for pick in llm_picks:
            name = pick.get("gem", "").strip()
            role = pick.get("role", "").strip()
            cand = cand_map.get(name.lower())
            if not cand:
                continue

            cat = cand["category"]
            if cat_counts.get(cat, 0) >= 3:
                continue

            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            cand_source = cand.get("source", "match")
            final_role = role or self._generate_contextual_role(cand["gem"], cand["category"], "", cand_source)
            if cand_source == "preferred" and "user preferred" not in final_role.lower():
                final_role = f"user preferred pick - {final_role}"

            selected.append({
                "gem": cand["gem"],
                "category": cand["category"],
                "layer": cand["layer"],
                "role": final_role,
                "source": cand_source,
            })
            if len(selected) >= max_gems:
                break

        return selected

    def build_stack(
        self,
        context_query: str,
        max_gems: int = 10,
        llm_service: Optional[LLMService] = None,
    ) -> dict:
        """Compose a curated, layered stack manifest for context_query.

        Retrieves candidates matching active categories, then applies second-pass
        curation (via LLM synthesis or offline fallback) to return a compact stack of
        5-12 gems with category limits and contextual roles.
        """
        retrieve_limit = max(self.limit, max_gems * 3)
        matches = self._retrieve(context_query, limit=retrieve_limit)

        # Stage 1: Identify relevant categories and gather relevance-gated candidate pool
        relevant_categories = self._identify_relevant_categories(context_query, matches)
        candidates = self._gather_candidate_pool(context_query, relevant_categories, matches)

        # Stage 2: Second-pass refinement (LLM when available, offline fallback)
        llm = llm_service or self.llm_service or (LLMService() if settings.llm_api_key else None)
        selected_curated: Optional[List[dict]] = None
        if llm is not None:
            try:
                llm_picks = llm.refine_stack_selection(context_query, candidates, max_gems=max_gems)
                if llm_picks:
                    selected_curated = self._apply_llm_selection(llm_picks, candidates, max_gems=max_gems)
            except Exception:
                selected_curated = None

        if not selected_curated:
            selected_curated = self._offline_refine_candidates(context_query, candidates, max_gems=max_gems)

        # Assemble into active layers only (omitting empty layers)
        layers = []
        for layer in LAYERS:
            layer_entries = [e for e in selected_curated if e["layer"] == layer]
            if layer_entries:
                layers.append({"layer": layer, "entries": layer_entries})

        return {"context": context_query, "layers": layers}

    @staticmethod
    def render_manifest_yaml(manifest: dict) -> str:
        return yaml.dump(manifest, sort_keys=False, allow_unicode=True)

    @staticmethod
    def render_gemfile(manifest: dict) -> str:
        lines = ['# Generated by "rubygemdb stack" -- advisory, not a dependency resolver',
                 'source "https://rubygems.org"']
        for layer in manifest.get("layers", []):
            lines.append("")
            lines.append(f"# {layer['layer']} layer")
            for entry in layer.get("entries", []):
                comment = entry.get("role", "")
                gem_line = f'gem "{entry["gem"]}"'
                if comment:
                    gem_line += f" # {comment}"
                lines.append(gem_line)
        return "\n".join(lines) + "\n"
