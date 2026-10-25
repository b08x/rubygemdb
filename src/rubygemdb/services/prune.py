"""Prune report generation for overlapping and stale gems.

Builds an approval-gated prune report before any re-classification:
- stale gems: no release (version_created_at) in 3+ years per RubyGems API
  metadata, sourced from the gem cache with fetch fallback for missing gems
- overlap candidates: name-normalization duplicates (e.g. standardrb vs
  standard). Within an overlap group the most recently released gem is
  kept; the others are pruned.
- shared-URI groups: gems sharing a normalized source_code_uri (monorepo
  families). Reported for review only — distinct gems commonly ship from
  one repository, so these are NOT auto-pruned by --apply-prune.

Writes data/prune-report.md for review. Nothing is removed until the user
approves via `rubygemdb reclassify --apply-prune`.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from rubygemdb.core.config import settings
from rubygemdb.services.rubygems import RubyGemsService

STALE_YEARS = 3
_RUBY_WRAPPERS = ("ruby", "rb")
# Remainder must stay this long after stripping a wrapper token, so
# 'ruby-progressbar' -> 'progressbar' collides but 'iruby' -> 'i' does not
# (iruby and irb are unrelated gems).
_MIN_REMAINDER = 3
_NORMALIZE_RE = re.compile(r"[^a-z0-9]")


def normalize_name(name: str) -> str:
    """Normalize a gem name for duplicate detection.

    Strips separators, then removes leading/trailing 'ruby'/'rb' wrapper
    tokens so 'standardrb' and 'standard', 'yajl-ruby' and 'yajl' collide.
    """
    n = _NORMALIZE_RE.sub("", name.lower())
    for wrapper in _RUBY_WRAPPERS:
        if n.startswith(wrapper) and len(n) - len(wrapper) >= _MIN_REMAINDER:
            n = n[len(wrapper):]
        if n.endswith(wrapper) and len(n) - len(wrapper) >= _MIN_REMAINDER:
            n = n[:-len(wrapper)]
    return n


def normalize_uri(uri: str) -> str:
    """Normalize a source URI: strip protocol, www, trailing slashes, .git,
    and GitHub version-pin (/tree/...) suffixes."""
    u = uri.lower().strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.rstrip("/")
    u = re.sub(r"\.git$", "", u)
    u = re.sub(r"/(tree|blob|tag|commit)/[^/]+$", "", u)
    return u.rstrip("/")


@dataclass
class PruneCandidate:
    name: str
    reason: str
    detail: str = ""


@dataclass
class PruneReport:
    stale: List[PruneCandidate] = field(default_factory=list)
    overlaps: List[PruneCandidate] = field(default_factory=list)
    # Gems whose cached RubyGems API entry points at a different repository
    # than the inventory row — usually a name collision (e.g. the 2011
    # 'huh' unit-testing gem vs marcoroth/huh-ruby). Review only: the API
    # entry's release data describes the wrong gem, so staleness is skipped.
    mismatches: List[PruneCandidate] = field(default_factory=list)
    # Gems sharing a normalized source URI (monorepo families etc.).
    # Reported for review but NOT auto-pruned by --apply-prune: distinct gems
    # commonly ship from one repository.
    uri_groups: List[List[PruneCandidate]] = field(default_factory=list)

    @property
    def all_names(self) -> List[str]:
        """Names that --apply-prune would remove (stale + name-overlap prunables)."""
        names = [c.name for c in self.stale]
        names.extend(c.name for c in self.overlaps)
        return sorted(set(names))


class PruneService:
    def __init__(self, rubygems_service: Optional[RubyGemsService] = None):
        self.rubygems = rubygems_service or RubyGemsService()

    def _version_created_at(self, name: str, gem_cache: dict) -> Optional[datetime]:
        info = gem_cache.get(name)
        if info is None:
            info = self.rubygems.fetch_gem_info(name) or {}
        raw = info.get("version_created_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _record_overlap_group(self, report: PruneReport, group: List[dict], reason: str):
        """Keep the most recently released gem in the group; prune the rest."""
        dated = [(self._version_created_at(g["name"], self._cache) or datetime.min.replace(tzinfo=timezone.utc), g) for g in group]
        dated.sort(key=lambda x: x[0], reverse=True)
        kept = dated[0][1]
        for _, gem in dated[1:]:
            report.overlaps.append(PruneCandidate(
                name=gem["name"],
                reason=reason,
                detail=f"kept '{kept['name']}' (more recent release)",
            ))

    def build_report(self, gems: List[dict], gem_cache: Optional[dict] = None) -> PruneReport:
        """gems: list of inventory dicts with name + source_code_uri keys."""
        report = PruneReport()
        self._cache = gem_cache if gem_cache is not None else (self.rubygems.cache or {})
        now = datetime.now(timezone.utc)

        # (a) stale gems — but never trust API data from a name collision:
        # if the cached API entry points at a different repo than the
        # inventory row, its release date describes the wrong gem.
        for gem in gems:
            name = gem["name"]
            info = self._cache.get(name)
            if info is None:
                info = self.rubygems.fetch_gem_info(name) or {}
            inv_uri = gem.get("source_code_uri") or ""
            api_uri = info.get("source_code_uri") or ""
            if inv_uri and api_uri and normalize_uri(inv_uri) != normalize_uri(api_uri):
                report.mismatches.append(PruneCandidate(
                    name=name,
                    reason="metadata mismatch: RubyGems entry points at a different repository",
                    detail=f"inventory: {inv_uri} vs API: {api_uri}",
                ))
                continue

            raw = info.get("version_created_at")
            if not raw:
                continue
            try:
                created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            age_days = (now - created).days
            if age_days >= STALE_YEARS * 365:
                report.stale.append(PruneCandidate(
                    name=name,
                    reason=f"stale: no release in {age_days // 365} years",
                    detail=f"last release {created.date().isoformat()}",
                ))

        # (b) overlap candidates by normalized name
        by_norm: dict[str, list[dict]] = {}
        for gem in gems:
            by_norm.setdefault(normalize_name(gem["name"]), []).append(gem)
        for norm, group in sorted(by_norm.items()):
            if len(group) > 1:
                self._record_overlap_group(
                    report, group, f"overlap: normalized name '{norm}' shared"
                )

        # (c) gems sharing a normalized source_code_uri — review only,
        # distinct gems commonly ship from one repo (monorepo families).
        by_uri: dict[str, list[dict]] = {}
        for gem in gems:
            uri = gem.get("source_code_uri") or ""
            if not uri:
                continue
            by_uri.setdefault(normalize_uri(uri), []).append(gem)
        for uri, group in sorted(by_uri.items()):
            if len(group) > 1:
                report.uri_groups.append([
                    PruneCandidate(
                        name=g["name"],
                        reason=f"shared source URI {uri}",
                        detail="review: same repository, distinct gems are not pruned automatically",
                    )
                    for g in group
                ])

        return report

    def render_markdown(self, report: PruneReport) -> str:
        lines = ["# Prune Report", ""]
        lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
        lines.append("")
        lines.append(f"- Stale gems (no release in {STALE_YEARS}+ years): **{len(report.stale)}**")
        lines.append(f"- Overlap prunables (newest of each group kept): **{len(report.overlaps)}**")
        lines.append(f"- Metadata mismatches (review only): **{len(report.mismatches)}**")
        uri_review_names = len({c.name for group in report.uri_groups for c in group})
        lines.append(f"- Shared-URI groups (review only, not auto-pruned): **{len(report.uri_groups)}** groups, {uri_review_names} gems")
        lines.append(f"- Total gems --apply-prune would remove: **{len(report.all_names)}**")
        lines.append("")

        lines.append("## Stale gems")
        lines.append("")
        if report.stale:
            lines.append("| Gem | Detail |")
            lines.append("|---|---|")
            for c in report.stale:
                lines.append(f"| {c.name} | {c.reason} ({c.detail}) |")
        else:
            lines.append("None.")
        lines.append("")

        lines.append("## Overlap candidates")
        lines.append("")
        if report.overlaps:
            lines.append("| Gem | Reason | Detail |")
            lines.append("|---|---|---|")
            for c in report.overlaps:
                lines.append(f"| {c.name} | {c.reason} | {c.detail} |")
        else:
            lines.append("None.")
        lines.append("")

        lines.append("## Metadata mismatches (review only)")
        lines.append("")
        lines.append("The cached RubyGems API entry for these gems points at a different")
        lines.append("repository than the inventory row — usually a name collision with an")
        lines.append("unrelated gem that owns the name on rubygems.org. Their release data")
        lines.append("describes the wrong gem, so staleness is skipped for them.")
        lines.append("")
        if report.mismatches:
            lines.append("| Gem | Inventory URI | API URI |")
            lines.append("|---|---|---|")
            for c in report.mismatches:
                inventory_uri, _, api_uri = c.detail.partition(" vs ")
                lines.append(f"| {c.name} | {inventory_uri.replace('inventory: ', '')} | {api_uri.replace('API: ', '')} |")
        else:
            lines.append("None.")
        lines.append("")

        lines.append("## Shared-URI groups (review only)")
        lines.append("")
        lines.append("Distinct gems commonly ship from one repository (monorepo families like")
        lines.append("`dspy-*` or `google-cloud-*`). These groups are reported for manual review")
        lines.append("but are NOT removed by --apply-prune.")
        lines.append("")
        if report.uri_groups:
            for i, group in enumerate(report.uri_groups, start=1):
                names = ", ".join(c.name for c in group)
                lines.append(f"{i}. **{names}** — {group[0].reason}")
        else:
            lines.append("None.")
        lines.append("")

        lines.append("## Approval")
        lines.append("")
        lines.append(
            "Nothing is deleted by this report. Review it, then run "
            "`rubygemdb reclassify --apply-prune` to remove the gems listed "
            "in the stale and overlap sections above before reclassification."
        )
        return "\n".join(lines)

    def write_report(self, report: PruneReport, path=None) -> str:
        path = path or settings.prune_report_file
        content = self.render_markdown(report)
        with open(path, "w") as f:
            f.write(content)
        return str(path)


_GEM_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_HEADER_WORDS = {
    "gem", "gems", "detail", "reason", "name", "action", "inventory uri",
    "api uri", "stale gems", "overlap prunables", "metadata mismatches", "shared-uri groups",
}


def clean_gem_token(token: str) -> str:
    """Clean markdown styling (backticks, bold asterisks, brackets) from a gem token."""
    return re.sub(r"[`*\[\]]", "", token).strip()


def parse_prune_candidates(path_or_content: Union[str, Path]) -> List[PruneCandidate]:
    """Parse prune candidates (names and optional reasons/details) from a file or text string.

    Supports:
    - Markdown tables: | Gem | Reason | Detail | or | Gem | Detail |
    - Markdown lists: - gem, * gem, 1. gem, with optional descriptions
    - Plain text: one gem per line, '#' comments allowed
    - Comma-separated gem names
    """
    if isinstance(path_or_content, Path) or (
        isinstance(path_or_content, str)
        and "\n" not in path_or_content
        and Path(path_or_content).exists()
    ):
        text = Path(path_or_content).read_text(encoding="utf-8")
    else:
        text = str(path_or_content)

    candidates: List[PruneCandidate] = []
    seen: set[str] = set()

    def add_candidate(name: str, reason: str = "", detail: str = "") -> None:
        cleaned_name = clean_gem_token(name)
        if not cleaned_name or not _GEM_NAME_RE.match(cleaned_name):
            return
        if cleaned_name.lower() in _HEADER_WORDS:
            return
        if cleaned_name not in seen:
            seen.add(cleaned_name)
            candidates.append(PruneCandidate(name=cleaned_name, reason=reason.strip(), detail=detail.strip()))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Comment line
        if line.startswith("#"):
            continue
        # Strip inline comment
        if " #" in line:
            line = line.split(" #", 1)[0].strip()

        # Markdown table row: | cell1 | cell2 | ... |
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if cells:
                col1 = clean_gem_token(cells[0])
                if not col1 or re.match(r"^:?-+:?$", col1):
                    continue
                reason = cells[1] if len(cells) > 1 else ""
                detail = cells[2] if len(cells) > 2 else ""
                add_candidate(col1, reason=reason, detail=detail)
            continue

        # Markdown list item: - gem, * gem, 1. gem
        list_match = re.match(r"^(?:[-*]|\d+\.)\s+(.+)$", line)
        if list_match:
            item_body = list_match.group(1).strip()
            # Ignore summary lines like "Stale gems (no release...): **212**"
            if re.match(r"^[A-Z][a-zA-Z\s]+.*:\s*\*\*\d+\*\*", item_body):
                continue
            reason = ""
            if "—" in item_body:
                gem_part, reason = item_body.split("—", 1)
            elif ":" in item_body and not item_body.startswith("http"):
                gem_part, reason = item_body.split(":", 1)
            else:
                gem_part = item_body
            for token in gem_part.split(","):
                add_candidate(token, reason=reason)
            continue

        # Comma-separated or whitespace tokens
        for token in line.split(","):
            add_candidate(token)

    return candidates


def parse_prune_file(path_or_content: Union[str, Path]) -> List[str]:
    """Extract list of unique gem names from a prune file or text string."""
    return [c.name for c in parse_prune_candidates(path_or_content)]
