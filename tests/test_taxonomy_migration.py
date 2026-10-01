"""Migration checks: new taxonomy fully wired, old 12 slugs gone."""
import subprocess
from pathlib import Path

from rubygemdb.services.llm import LLMService
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.models.categories import VALID_CATEGORIES
from unittest.mock import MagicMock, Mock

SRC_DIR = Path(__file__).parent.parent / "src"

OLD_SLUGS = [
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
]


def test_llm_prompt_contains_all_new_slugs():
    llm = LLMService()
    prompt = llm.build_prompt("test-gem", {"info": "test"}, [])
    for slug in VALID_CATEGORIES:
        assert slug in prompt, f"LLM prompt missing category: {slug}"


def test_llm_prompt_contains_no_old_slugs():
    llm = LLMService()
    prompt = llm.build_prompt("test-gem", {"info": "test"}, [])
    for old in OLD_SLUGS:
        assert old not in prompt, f"LLM prompt contains old category: {old}"


def test_no_old_slugs_anywhere_under_src():
    result = subprocess.run(
        ["grep", "-r", "-l", "-E", "|".join(OLD_SLUGS), str(SRC_DIR)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1, f"Old slugs still present in src/:\n{result.stdout}"


def test_classifier_emits_only_new_slugs():
    rg = MagicMock()
    llm = MagicMock()
    scorer = Mock()
    scorer.score = lambda n, d: {}
    classifier = GemClassifier(rg, llm, embedding_scorer=scorer)

    def info(desc="A ruby gem"):
        return {"dependencies": {"runtime": []}, "platform": "ruby", "info": desc}

    for name in ("rails", "thor", "sidekiq", "nokogiri", "pry", "bubbles", "zzz-unknown"):
        cls, _, _, _ = classifier.heuristic_classify(name, info())
        assert cls.primary in VALID_CATEGORIES, f"{name} produced {cls.primary}"
        for old in OLD_SLUGS:
            assert cls.primary != old


def test_risk_fallback_returns_defaults_for_unknown_slug():
    classifier = GemClassifier(MagicMock(), MagicMock())
    risks = classifier.score_gem("some_unknown_slug", [])
    assert risks.invasiveness == 3
    assert risks.abstraction_leak == "low"
    assert risks.coupling == 1
