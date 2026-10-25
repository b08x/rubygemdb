"""Import GitHub Starred Gems Pipeline.

Discovers starred Ruby repositories, resolves them to published RubyGems,
enriches existing records, inserts new gems with inferred GenAI backend tooling roles,
runs an approval-gated deduplication and staleness prune audit, and optionally
classifies and embeds the results.
"""

import logging
import os
from pathlib import Path
from typing import Any, Callable, List, Optional
import yaml  # type: ignore

from rubygemdb.models.gem import GemEntry
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.services.github_stars import GitHubStarsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.prune import PruneReport, PruneService, normalize_uri, parse_prune_file
from rubygemdb.storage.sqlite_storage import SQLiteStorage

logger = logging.getLogger(__name__)


def load_prune_list(path: str) -> List[str]:
    """Load an approved prune list from a text file or markdown report."""
    return parse_prune_file(path)


class ImportStarsPipeline:
    def __init__(
        self,
        storage: Optional[SQLiteStorage] = None,
        stars_service: Optional[GitHubStarsService] = None,
        classifier: Optional[GemClassifier] = None,
        prune_service: Optional[PruneService] = None,
        user: str = "b08x",
        language: Optional[str] = "Ruby",
        dry_run: bool = False,
        classify: bool = False,
        embed: bool = False,
        apply_prune: bool = False,
        select_prune: bool = False,
        prune_list: Optional[str] = None,
        backend: str = "auto",
        out_dir: str = "output",
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ):
        self.storage = storage or SQLiteStorage()
        self.stars_service = stars_service or GitHubStarsService(
            rubygems_service=self.storage.rubygems,
            context7_service=self.storage.context7,
        )
        self.classifier = classifier or GemClassifier(
            rubygems_service=self.storage.rubygems,
            llm_service=LLMService(),
        )
        self.prune_service = prune_service or PruneService(
            rubygems_service=self.storage.rubygems,
        )
        self.user = user
        self.language = language
        self.dry_run = dry_run
        self.classify = classify
        self.embed = embed
        self.apply_prune = apply_prune
        self.select_prune = select_prune
        self.prune_list = prune_list
        self.backend = backend
        self.out_dir = out_dir
        self.progress_callback = progress_callback

    def _notify(self, message: str, current: int, total: int):
        if self.progress_callback:
            self.progress_callback(message, current, total)
        else:
            logger.info(f"[{current}/{total}] {message}")

    def run(self) -> dict[str, Any]:
        """Execute the starred repositories discovery, verification, and import pipeline."""
        # 1. Fetch Starred Repositories
        logger.info(f"Fetching starred repositories for @{self.user} (language: {self.language or 'all'})...")
        repos = self.stars_service.fetch_starred_repos(user=self.user, language=self.language)
        total_repos = len(repos)
        logger.info(f"Retrieved {total_repos} starred repositories matching filter.")

        # 2. Resolve Repositories to Published RubyGems
        resolved_gems: List[dict[str, Any]] = []
        skipped_repos: List[dict[str, Any]] = []

        for idx, repo in enumerate(repos, start=1):
            repo_name = repo.get("name", "unknown")
            self._notify(f"Resolving gem for repo {repo_name}...", idx, total_repos)
            gem_data = self.stars_service.resolve_gem_for_repo(repo, rubygems_service=self.storage.rubygems)
            if gem_data:
                resolved_gems.append(gem_data)
            else:
                skipped_repos.append(repo)

        logger.info(f"Resolved {len(resolved_gems)} published gems; skipped {len(skipped_repos)} non-gem repositories.")

        # 3. Deduplication & Inventory Enrichment
        existing_inventory = self.storage.get_all_inventory_gems()
        existing_by_name = {g["name"].lower(): g for g in existing_inventory}
        existing_by_uri = {
            normalize_uri(g["source_code_uri"]): g
            for g in existing_inventory
            if g.get("source_code_uri")
        }

        new_gems: List[dict[str, Any]] = []
        enriched_gems: List[dict[str, Any]] = []
        unchanged_gems: List[dict[str, Any]] = []

        for gem_data in resolved_gems:
            name = gem_data["name"]
            name_l = name.lower()
            source_uri = gem_data.get("source_code_uri") or ""
            norm_uri = normalize_uri(source_uri) if source_uri else ""

            # Check if gem exists in inventory
            if name_l in existing_by_name:
                curr = existing_by_name[name_l]
                # Determine missing fields
                needed_updates: dict[str, Any] = {}
                if not curr.get("homepage") and gem_data.get("homepage"):
                    needed_updates["homepage"] = gem_data["homepage"]
                if not curr.get("source_code_uri") and gem_data.get("source_code_uri"):
                    needed_updates["source_code_uri"] = gem_data["source_code_uri"]
                if not curr.get("context7_id") and gem_data.get("context7_id"):
                    needed_updates["context7_id"] = gem_data["context7_id"]
                if not curr.get("description") and gem_data.get("description"):
                    needed_updates["description"] = gem_data["description"]
                if not curr.get("genai_uses"):
                    inferred_genai = self.classifier.infer_genai_tooling_uses(
                        name=name,
                        description=gem_data.get("description") or curr.get("description") or "",
                        category="ai_llm",
                        sub_categories=[],
                    )
                    needed_updates["genai_uses"] = inferred_genai
                    gem_data["genai_uses"] = inferred_genai

                if needed_updates:
                    if not self.dry_run:
                        self.storage.enrich_inventory_gem(
                            name=curr["name"],
                            homepage=needed_updates.get("homepage"),
                            source_code_uri=needed_updates.get("source_code_uri"),
                            description=needed_updates.get("description"),
                            context7_id=needed_updates.get("context7_id"),
                            genai_uses=needed_updates.get("genai_uses"),
                        )
                    enriched_gems.append({**gem_data, "enrichments": list(needed_updates.keys())})
                else:
                    unchanged_gems.append(gem_data)
            else:
                # New gem
                inferred_genai = self.classifier.infer_genai_tooling_uses(
                    name=name,
                    description=gem_data.get("description") or "",
                    category="ai_llm",
                    sub_categories=[],
                )
                gem_data["genai_uses"] = inferred_genai
                if not self.dry_run:
                    self.storage.upsert_inventory_gem(
                        name=name,
                        version=gem_data.get("version"),
                        description=gem_data.get("description"),
                        homepage=gem_data.get("homepage"),
                        source_code_uri=gem_data.get("source_code_uri"),
                        context7_id=gem_data.get("context7_id"),
                        genai_uses=inferred_genai,
                        verified=1,
                    )
                new_gems.append(gem_data)
                # Track in map to avoid self-collision if starred list has aliases
                existing_by_name[name_l] = gem_data
                if norm_uri:
                    existing_by_uri[norm_uri] = gem_data

        logger.info(
            f"Inventory merge: {len(new_gems)} new, {len(enriched_gems)} enriched, {len(unchanged_gems)} unchanged."
        )

        # 4. Prune Audit Pass
        # Compile complete inventory view for prune evaluation
        if self.dry_run:
            # Simulate merged inventory
            combined_inventory = list(existing_inventory)
            combined_inventory.extend(new_gems)
        else:
            combined_inventory = self.storage.get_all_inventory_gems()

        report: PruneReport = self.prune_service.build_report(combined_inventory)
        report_file: Optional[str] = None
        if not self.dry_run:
            report_file = self.prune_service.write_report(report)
            logger.info(f"Prune report written to {report_file}")

        pruned_gems: List[str] = []
        if self.select_prune:
            from rubygemdb.ui.selector import select_gems_to_prune
            candidates = report.stale + report.overlaps
            selected = select_gems_to_prune(candidates, header="Select audit candidates to prune:", backend=self.backend)
            inv_names = {g["name"] for g in combined_inventory}
            prune_targets = [g for g in selected if g in inv_names]

            if not self.dry_run:
                for target in prune_targets:
                    self.storage.delete_gem(target)
                pruned_gems = prune_targets
                logger.info(f"Pruned {len(pruned_gems)} gems selected via multi-selector.")
            else:
                pruned_gems = prune_targets
                logger.info(f"[Dry Run] Would prune {len(pruned_gems)} gems selected via multi-selector.")
        elif self.apply_prune or self.prune_list:
            if self.prune_list:
                candidates_to_prune = load_prune_list(self.prune_list)
            else:
                candidates_to_prune = report.all_names

            # Only prune gems that actually exist in inventory
            inv_names = {g["name"] for g in combined_inventory}
            prune_targets = [g for g in candidates_to_prune if g in inv_names]

            if not self.dry_run:
                for target in prune_targets:
                    self.storage.delete_gem(target)
                pruned_gems = prune_targets
                logger.info(f"Pruned {len(pruned_gems)} gems from catalog.")
            else:
                pruned_gems = prune_targets
                logger.info(f"[Dry Run] Would prune {len(pruned_gems)} gems from catalog.")

        # 5. Optional Classification & Embedding
        classified_gems: List[GemEntry] = []
        if self.classify and not self.dry_run:
            logger.info(f"Classifying {len(new_gems)} new gems...")
            categorized: dict[str, list[dict[str, Any]]] = {}

            for gem in new_gems:
                entry = self.classifier.classify(
                    name=gem["name"],
                    category=None,
                    homepage=gem.get("homepage"),
                    source_code_uri=gem.get("source_code_uri"),
                    context7_id=gem.get("context7_id"),
                )
                classified_gems.append(entry)
                primary = entry.classification.primary
                categorized.setdefault(primary, []).append(entry.model_dump())

            if classified_gems:
                self.storage.save_classified_gems(classified_gems)
                os.makedirs(self.out_dir, exist_ok=True)
                for cat, items in categorized.items():
                    out_path = Path(self.out_dir) / f"{cat}.yaml"
                    existing_items: List[dict[str, Any]] = []
                    if out_path.exists():
                        try:
                            with open(out_path, "r") as f:
                                loaded = yaml.safe_load(f)
                                if isinstance(loaded, list):
                                    existing_items = loaded
                        except Exception:
                            existing_items = []
                    # Append new items ensuring no duplicate names in yaml
                    existing_names = {item.get("name") for item in existing_items}
                    merged_items = list(existing_items)
                    for itm in items:
                        if itm.get("name") not in existing_names:
                            merged_items.append(itm)
                    with open(out_path, "w") as f:
                        yaml.dump(merged_items, f, sort_keys=False)

        if self.embed and not self.dry_run:
            logger.info("Generating txtai vector embeddings index...")
            try:
                from rubygemdb.agent import TxtaiAgent
                agent = TxtaiAgent()
                agent.build_index()
                logger.info("Embeddings vectorization complete.")
            except Exception as e:
                logger.error(f"Error during embeddings generation: {e}")

        return {
            "user": self.user,
            "language": self.language,
            "dry_run": self.dry_run,
            "discovered_count": total_repos,
            "resolved_gems": resolved_gems,
            "new_gems": new_gems,
            "enriched_gems": enriched_gems,
            "unchanged_gems": unchanged_gems,
            "skipped_repos": skipped_repos,
            "prune_report": report,
            "prune_report_file": report_file,
            "pruned_gems": pruned_gems,
            "classified_gems": classified_gems,
        }
