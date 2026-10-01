"""Layered gem-stack composition for back-end tooling design.

Turns a context query into a layered stack manifest (substrate -> quality)
plus a Gemfile snippet. Candidates are retrieved via the txtai index; the
curated base_gems seeds from the taxonomy module are injected so every
manifest starts from the standard base picks (dotenv, drydock, pry,
rubocop, journald-logger, ...). Seeds missing from the inventory are added
so they are always retrievable.
"""

import re
from typing import List

import yaml  # type: ignore

from rubygemdb.core.config import settings
from rubygemdb.models.categories import CATEGORIES, CATEGORY_BY_SLUG, LAYERS

_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_GEM_NAME_RE = re.compile(r"^Gem Name: (\S+)")


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
    def __init__(self, index=None, storage=None, limit: int = 12):
        """index: txtai Embeddings (or a compatible mock with .search).
        storage: SQLiteStorage (or compatible mock) for seed inventory adds."""
        self._index = index
        self.storage = storage
        self.limit = limit

    def _load_index(self):
        from txtai import Embeddings  # type: ignore

        index_path = settings.txtai_dir / "rubygems_index"
        if not index_path.exists():
            return None
        try:
            embeddings = Embeddings(path="ollama/embeddinggemma", backend="sqlite", content=True, gpu=settings.txtai_gpu)
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

    def _retrieve(self, query: str) -> List[dict]:
        index = self.index
        if index is None:
            return []
        results = index.search(query, self.limit)
        matches = []
        for r in results:
            if isinstance(r, (tuple, list)) and len(r) == 2:
                r = {"id": r[0], "score": r[1]}
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

    def build_stack(self, context_query: str) -> dict:
        matches = self._retrieve(context_query)

        # Ensure curated seeds exist in the inventory so they stay retrievable.
        seeds = seed_gems()
        if self.storage is not None:
            for seed in seeds:
                try:
                    self.storage.add_gem_to_inventory(seed["gem"])
                except Exception:
                    pass

        entries: List[dict] = []
        seen = set()
        for seed in seeds:
            if seed["gem"] in seen:
                continue
            seen.add(seed["gem"])
            entries.append({
                "gem": seed["gem"],
                "category": seed["category"],
                "layer": seed["layer"],
                "role": "curated base pick for back-end tooling",
                "source": "seed",
            })
        for m in matches:
            if m["gem"] in seen:
                continue
            seen.add(m["gem"])
            entries.append({
                "gem": m["gem"],
                "category": m["category"],
                "layer": m["layer"],
                "role": f"context match (score {m['score']:.3f})",
                "source": "match",
            })

        layers = []
        for layer in LAYERS:
            layer_entries = [e for e in entries if e["layer"] == layer]
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
