"""Litmus classification tests: gems must land where a person expects."""
import pytest
from unittest.mock import MagicMock, Mock

from rubygemdb.services.classifier import GemClassifier
from rubygemdb.models.categories import VALID_CATEGORIES


@pytest.fixture
def classifier():
    """GemClassifier with mocked services and a no-op embedding scorer."""
    rg = MagicMock()
    llm = MagicMock()
    scorer = Mock()
    scorer.score = lambda name, description: {}
    return GemClassifier(rg, llm, embedding_scorer=scorer)


def _make_info(deps=None, platform=None, description="A ruby gem"):
    runtime_deps = [{"name": d} for d in (deps or [])]
    return {
        "dependencies": {"runtime": runtime_deps},
        "platform": platform,
        "info": description,
    }


# ── Litmus gems ─────────────────────────────────────────────────────

LITMUS = [
    ("jekyll", "static_site_generation"),
    ("nokogiri", "document_parsing"),
    ("thor", "cli_libraries"),
    ("sidekiq", "background_jobs"),
    ("ruby_llm", "ai_llm"),
]


@pytest.mark.parametrize("name,expected", LITMUS, ids=[n for n, _ in LITMUS])
def test_litmus(classifier, name, expected):
    cls, _, _, _ = classifier.heuristic_classify(name, _make_info())
    assert cls.primary == expected, f"{name} classified as {cls.primary}, expected {expected}"
    assert cls.confidence >= 0.7


# ── Spot checks across all 21 categories ────────────────────────────

SPOT_CHECKS = [
    ("rails", "web_frameworks"),
    ("faraday", "http_networking"),
    ("puma", "servers_concurrency"),
    ("sqlite3", "persistence"),
    ("yajl-ruby", "document_parsing"),
    ("prawn", "document_generation"),
    ("rspec", "testing_qa"),
    ("rubocop", "code_quality_typing"),
    ("dry-schema", "runtime_validation"),
    ("pry", "developer_tools"),
    ("fast-mcp", "mcp_tooling"),
    ("amatch", "text_search"),
    ("devise", "security_auth"),
    ("ruby-vips", "media_processing"),
    ("glimmer-dsl-libui", "gui_desktop"),
    ("ffi", "native_bindings"),
    ("activesupport", "core_extensions"),
]


@pytest.mark.parametrize("name,expected", SPOT_CHECKS, ids=[n for n, _ in SPOT_CHECKS])
def test_spot_checks(classifier, name, expected):
    cls, _, _, _ = classifier.heuristic_classify(name, _make_info())
    assert cls.primary == expected


def test_unknown_gem_defaults_low_confidence(classifier):
    cls, _, _, _ = classifier.heuristic_classify("bubbles", _make_info())
    assert cls.primary in VALID_CATEGORIES
    assert cls.confidence <= 0.7


def test_description_scoring_rescues_quiet_names(classifier):
    """A gem with a quiet name but explicit description lands correctly."""
    info = _make_info(description="A static site generator with blog awareness and Jekyll-compatible theming")
    cls, _, _, _ = classifier.heuristic_classify("beckett", info)
    assert cls.primary == "static_site_generation"


def test_development_group_hint_maps_to_testing_qa(classifier):
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("bubbles", info, category="development")
    assert cls.primary == "testing_qa"
    assert cls.confidence >= 0.7


def test_risk_scoring_rekeyed_to_new_slugs(classifier):
    risks = classifier.score_gem("web_frameworks", [])
    assert risks.invasiveness >= 4
    assert risks.abstraction_leak == "high"
    risks = classifier.score_gem("cli_libraries", [])
    assert risks.invasiveness <= 2
    assert risks.abstraction_leak == "low"


def test_unknown_slug_risk_fallback_without_raising(classifier):
    risks = classifier.score_gem("not_a_real_slug", ["a", "b"])
    assert risks.invasiveness == 3
    assert risks.abstraction_leak == "low"
    assert risks.coupling == 1


def test_attaches_to_from_taxonomy_module(classifier):
    rg = MagicMock()
    rg.fetch_gem_info.return_value = _make_info()
    llm = MagicMock()
    llm.call_llm.return_value = None
    scorer = Mock()
    scorer.score = lambda n, d: {}
    c = GemClassifier(rg, llm, embedding_scorer=scorer)
    entry = c.classify("thor")
    assert entry.role["attaches_to"] == "cli"
    assert entry.classification.primary == "cli_libraries"


def test_embedding_scorer_blends_into_classification():
    """A description-only gem with a decisive embedding score wins via blending."""
    rg = MagicMock()
    llm = MagicMock()
    scorer = Mock()
    # Only ai_llm scores; everything else near zero.
    scorer.score = lambda name, description: {"ai_llm": 0.98, "core_extensions": 0.05}
    c = GemClassifier(rg, llm, embedding_scorer=scorer)
    info = _make_info(description="Talks to hosted models")
    cls, _, _, _ = c.heuristic_classify("beckett", info)
    assert cls.primary == "ai_llm"


def test_embedding_scorer_failure_degrades_gracefully():
    """A raising scorer is swallowed; keyword scoring still works."""
    rg = MagicMock()
    llm = MagicMock()
    scorer = Mock()
    scorer.score = MagicMock(side_effect=RuntimeError("ollama down"))
    c = GemClassifier(rg, llm, embedding_scorer=scorer)
    cls, _, _, _ = c.heuristic_classify("thor", _make_info())
    assert cls.primary == "cli_libraries"
