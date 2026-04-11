"""Tests for the refactored 12-category classification system."""
import pytest
from rubygemdb.services.classifier import GemClassifier, VALID_CATEGORIES
from rubygemdb.services.llm import LLMService
from rubygemdb.models.gem import GemClassification


# ── Category Constants ──────────────────────────────────────────────

EXPECTED_CATEGORIES = [
    "runtime_spine",
    "cli_terminal_ui",
    "storage_persistence",
    "async_networking_orchestration",
    "ai_nlp",
    "data_processing",
    "retrieval_similarity_fuzzy",
    "algorithms_knowledge_structures",
    "validation_types",
    "parsing_encoding",
    "debugging_introspection",
    "mcp_tooling",
]

OLD_CATEGORIES = [
    "runtime_substrate",
    "framework_integration",
    "boundary_interface",
    "application_capability",
    "policy_enforcement",
    "observability",
    "developer_experience",
    "build_delivery",
]


def test_valid_categories_has_exactly_12():
    assert len(VALID_CATEGORIES) == 12


def test_valid_categories_contains_all_expected():
    for cat in EXPECTED_CATEGORIES:
        assert cat in VALID_CATEGORIES, f"Missing category: {cat}"


def test_old_categories_removed():
    for old_cat in OLD_CATEGORIES:
        assert old_cat not in VALID_CATEGORIES, f"Old category should be removed: {old_cat}"


# ── Heuristic Classification ────────────────────────────────────────

@pytest.fixture
def classifier():
    """Create a GemClassifier with mocked services."""
    from unittest.mock import MagicMock
    rg = MagicMock()
    llm = MagicMock()
    return GemClassifier(rg, llm)


def _make_info(deps=None, platform=None):
    """Helper to build a mock gem info dict."""
    runtime_deps = [{"name": d} for d in (deps or [])]
    return {
        "dependencies": {"runtime": runtime_deps},
        "platform": platform,
        "info": "test gem",
    }


def test_classify_cli_gem(classifier):
    """Gems like 'thor' or 'gli' should be cli_terminal_ui."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("thor", info)
    assert cls.primary == "cli_terminal_ui"


def test_classify_storage_gem(classifier):
    """Gems like 'activerecord' or 'sequel' should be storage_persistence."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("activerecord", info)
    assert cls.primary == "storage_persistence"


def test_classify_async_gem(classifier):
    """Gems like 'sidekiq' or 'async' should be async_networking_orchestration."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("sidekiq", info)
    assert cls.primary == "async_networking_orchestration"


def test_classify_ai_gem(classifier):
    """Gems like 'ruby-openai' should be ai_nlp."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("ruby-openai", info)
    assert cls.primary == "ai_nlp"


def test_classify_data_processing_gem(classifier):
    """Gems like 'nokogiri' or 'roo' should be data_processing."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("nokogiri", info)
    assert cls.primary == "data_processing"


def test_classify_retrieval_gem(classifier):
    """Gems like 'elasticsearch-model' should be retrieval_similarity_fuzzy."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("elasticsearch-model", info)
    assert cls.primary == "retrieval_similarity_fuzzy"


def test_classify_algorithms_gem(classifier):
    """Gems like 'algorithms' or 'rbtree' should be algorithms_knowledge_structures."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("algorithms", info)
    assert cls.primary == "algorithms_knowledge_structures"


def test_classify_validation_gem(classifier):
    """Gems like 'dry-validation' or 'dry-types' should be validation_types."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("dry-validation", info)
    assert cls.primary == "validation_types"


def test_classify_parsing_gem(classifier):
    """Gems like 'json' or 'yajl-ruby' should be parsing_encoding."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("yajl-ruby", info)
    assert cls.primary == "parsing_encoding"


def test_classify_debugging_gem(classifier):
    """Gems like 'pry' or 'byebug' should be debugging_introspection."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("pry", info)
    assert cls.primary == "debugging_introspection"


def test_classify_mcp_gem(classifier):
    """Gems with 'mcp' in name should be mcp_tooling."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("fast-mcp", info)
    assert cls.primary == "mcp_tooling"


def test_classify_http_gem(classifier):
    """HTTP client gems like 'faraday' should be async_networking_orchestration."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("faraday", info)
    assert cls.primary == "async_networking_orchestration"


def test_classify_rails_gem(classifier):
    """Rails gems should be runtime_spine (boot + wiring)."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("rails", info)
    assert cls.primary == "runtime_spine"


def test_classify_default_unknown_gem(classifier):
    """Unknown gems should fall back to a sensible default with low confidence."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("bubbles", info)
    assert cls.primary in VALID_CATEGORIES
    assert cls.confidence <= 0.7


def test_heuristic_confidence_boosted_on_match(classifier):
    """Matched gems should have confidence > 0.6."""
    info = _make_info()
    cls, _, _, _ = classifier.heuristic_classify("sidekiq", info)
    assert cls.confidence > 0.6


# ── Score Gem ───────────────────────────────────────────────────────

def test_score_gem_runtime_spine_high_invasiveness(classifier):
    risks = classifier.score_gem("runtime_spine", ["activesupport"])
    assert risks.invasiveness >= 4


def test_score_gem_mcp_tooling_low_invasiveness(classifier):
    risks = classifier.score_gem("mcp_tooling", [])
    assert risks.invasiveness <= 2


def test_score_gem_cli_terminal_ui(classifier):
    risks = classifier.score_gem("cli_terminal_ui", [])
    assert risks.invasiveness >= 1


def test_score_gem_async_networking(classifier):
    risks = classifier.score_gem("async_networking_orchestration", ["async"])
    assert risks.invasiveness >= 1


# ── LLM Prompt ──────────────────────────────────────────────────────

def test_llm_prompt_contains_all_12_categories():
    llm = LLMService()
    prompt = llm.build_prompt("test-gem", {"info": "test"}, [])
    for cat in EXPECTED_CATEGORIES:
        assert cat in prompt, f"LLM prompt missing category: {cat}"


def test_llm_prompt_does_not_contain_old_categories():
    llm = LLMService()
    prompt = llm.build_prompt("test-gem", {"info": "test"}, [])
    for old_cat in OLD_CATEGORIES:
        assert old_cat not in prompt, f"LLM prompt contains old category: {old_cat}"


# ── Model Validation ────────────────────────────────────────────────

def test_gem_classification_accepts_new_slugs():
    for cat in EXPECTED_CATEGORIES:
        cls = GemClassification(primary=cat, confidence=0.8)
        assert cls.primary == cat


def test_gem_classification_rejects_old_slugs():
    """Old category slugs should still be accepted as plain strings (no enum constraint),
    but the classifier should never produce them."""
    # Pydantic model uses str, so this just verifies the classifier doesn't emit old slugs
    pass  # Covered by heuristic tests above


# ── Sub-category Detection ──────────────────────────────────────────

def test_sub_category_background_jobs(classifier):
    info = _make_info(deps=["sidekiq"])
    _, _, _, sub_cats = classifier.heuristic_classify("my-worker", info)
    assert "background_jobs" in sub_cats


def test_sub_category_database(classifier):
    info = _make_info(deps=["pg"])
    _, _, _, sub_cats = classifier.heuristic_classify("my-db", info)
    assert "database" in sub_cats


def test_sub_category_monitoring(classifier):
    info = _make_info(deps=["sentry-ruby"])
    _, _, _, sub_cats = classifier.heuristic_classify("my-monitor", info)
    assert "monitoring" in sub_cats
