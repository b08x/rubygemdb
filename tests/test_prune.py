"""Prune report tests: normalization, staleness, and safe auto-prune sets."""
from datetime import datetime, timedelta, timezone

from rubygemdb.services.prune import (
    PruneService,
    normalize_name,
    normalize_uri,
)


class FakeRubyGems:
    """RubyGems-like service backed by an in-memory cache."""

    def __init__(self, cache):
        self.cache = cache

    def fetch_gem_info(self, name):
        return self.cache.get(name)


def _cache_entry(released_days_ago):
    dt = datetime.now(timezone.utc) - timedelta(days=released_days_ago)
    return {"version_created_at": dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")}


def _gem(name, uri=None):
    return {"name": name, "source_code_uri": uri or f"https://github.com/x/{name}"}


# ── Name normalization ──────────────────────────────────────────────

def test_normalize_name_collides_wrapped_variants():
    assert normalize_name("standardrb") == normalize_name("standard")
    assert normalize_name("yajl-ruby") == normalize_name("yajl")
    assert normalize_name("ruby-progressbar") == normalize_name("progressbar")


def test_normalize_name_keeps_short_remainders_distinct():
    """iruby (Jupyter kernel) and irb (REPL) are unrelated gems."""
    assert normalize_name("iruby") != normalize_name("irb")


def test_normalize_uri():
    assert normalize_uri("https://GitHub.com/Acme/repo.git/") == "github.com/acme/repo"
    assert normalize_uri("http://www.acme.org/r/") == "acme.org/r"


# ── Report building ─────────────────────────────────────────────────

def _service(cache):
    return PruneService(rubygems_service=FakeRubyGems(cache))


def test_stale_gem_detected():
    service = _service({"oldgem": _cache_entry(4 * 365), "freshgem": _cache_entry(30)})
    report = service.build_report([_gem("oldgem"), _gem("freshgem")])
    assert [c.name for c in report.stale] == ["oldgem"]


def test_name_overlap_keeps_most_recent():
    cache = {
        "standard": _cache_entry(30),
        "standardrb": _cache_entry(4 * 365),
    }
    service = _service(cache)
    report = service.build_report([_gem("standard"), _gem("standardrb")])
    assert [c.name for c in report.overlaps] == ["standardrb"]
    assert "standardrb" in report.all_names
    assert "standard" not in report.all_names


def test_name_collision_skips_staleness():
    """A gem whose API entry points at a different repo is a collision,
    not a stale gem (the huh / huh-ruby case)."""
    cache = {
        "huh": {
            "version_created_at": "2011-03-09T00:20:20.370Z",
            "source_code_uri": "https://github.com/justinbaker/huh",
        }
    }
    service = _service(cache)
    report = service.build_report([_gem("huh", uri="https://github.com/marcoroth/huh-ruby")])
    assert report.stale == []
    assert len(report.mismatches) == 1
    assert report.mismatches[0].name == "huh"
    assert "huh" not in report.all_names


def test_matching_uri_still_scores_stale():
    cache = {
        "oldthing": {
            "version_created_at": "2011-03-09T00:20:20.370Z",
            "source_code_uri": "https://github.com/x/oldthing",
        }
    }
    service = _service(cache)
    report = service.build_report([_gem("oldthing", uri="https://github.com/x/oldthing")])
    assert [c.name for c in report.stale] == ["oldthing"]
    assert report.mismatches == []


def test_shared_uri_group_is_review_only():
    """dotenv and dotenv-rails share a repo but must not be auto-pruned."""
    cache = {"dotenv": _cache_entry(30), "dotenv-rails": _cache_entry(10)}
    service = _service(cache)
    report = service.build_report([
        _gem("dotenv", uri="https://github.com/bkeepers/dotenv"),
        _gem("dotenv-rails", uri="https://github.com/bkeepers/dotenv"),
    ])
    assert report.overlaps == []
    assert len(report.uri_groups) == 1
    group_names = {c.name for c in report.uri_groups[0]}
    assert group_names == {"dotenv", "dotenv-rails"}
    assert report.all_names == []  # nothing auto-pruned


def test_markdown_mentions_review_only_sections():
    service = _service({"dotenv": _cache_entry(30), "dotenv-rails": _cache_entry(10)})
    report = service.build_report([
        _gem("dotenv", uri="https://github.com/bkeepers/dotenv"),
        _gem("dotenv-rails", uri="https://github.com/bkeepers/dotenv"),
    ])
    md = service.render_markdown(report)
    assert "Shared-URI groups (review only)" in md
    assert "NOT removed by --apply-prune" in md or "not auto-pruned" in md.lower()


def test_service_uses_mock_rubygems():
    svc = PruneService(rubygems_service=FakeRubyGems({}))
    assert svc.rubygems.fetch_gem_info("nope") is None


def test_load_prune_list_parses_comments_and_blanks(tmp_path):
    from rubygemdb.cli import _load_prune_list
    f = tmp_path / "prune.txt"
    f.write_text("# keep standard, drop the rename duplicate\nstandardrb\n\n  # indented comment\nruby-progressbar\n")
    assert _load_prune_list(str(f)) == ["standardrb", "ruby-progressbar"]
