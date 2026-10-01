"""Taxonomy integrity tests for the 21-category module."""
from rubygemdb.models.categories import (
    CATEGORIES,
    CATEGORY_BY_SLUG,
    LAYERS,
    VALID_CATEGORIES,
)


def test_exactly_21_categories():
    assert len(CATEGORIES) == 21
    assert len(VALID_CATEGORIES) == 21


def test_unique_slugs():
    slugs = [c.slug for c in CATEGORIES]
    assert len(slugs) == len(set(slugs))


def test_labels_and_descriptions_present():
    for c in CATEGORIES:
        assert c.label, f"{c.slug} missing label"
        assert c.description, f"{c.slug} missing description"


def test_every_category_has_valid_layer():
    for c in CATEGORIES:
        assert c.layer in LAYERS, f"{c.slug} has invalid layer {c.layer}"


def test_layer_model_has_all_four_layers():
    assert LAYERS == ["substrate", "plumbing", "composition", "quality"]


def test_cli_libraries_subcategory_vocabulary():
    sub = CATEGORY_BY_SLUG["cli_libraries"].sub_categories
    for expected in ("cli_frameworks", "terminal_ui", "terminal_styling", "terminal_output"):
        assert expected in sub, f"cli_libraries missing sub-category {expected}"
        assert sub[expected], f"{expected} has no example gems"
    assert "thor" in sub["cli_frameworks"]
    assert "drydock" in sub["cli_frameworks"]


def test_ai_llm_subcategory_vocabulary():
    sub = CATEGORY_BY_SLUG["ai_llm"].sub_categories
    for expected in ("llm_clients", "agent_frameworks", "embeddings_vector", "prompt_tooling"):
        assert expected in sub, f"ai_llm missing sub-category {expected}"
        assert sub[expected], f"{expected} has no example gems"
    assert "ruby_llm" in sub["llm_clients"]


def test_base_gems_non_empty_for_substrate_and_quality():
    for c in CATEGORIES:
        if c.layer in ("substrate", "quality"):
            assert c.base_gems, f"{c.slug} ({c.layer}) has no base gems"


def test_common_base_picks_curated():
    all_base = {g for c in CATEGORIES for g in c.base_gems}
    for seed in ("dotenv", "drydock", "pry", "rubocop"):
        assert seed in all_base, f"curated base pick {seed} missing from taxonomy"


def test_category_by_slug_covers_all():
    assert set(CATEGORY_BY_SLUG.keys()) == set(VALID_CATEGORIES)


def test_expected_slugs():
    expected = {
        "web_frameworks", "http_networking", "servers_concurrency", "background_jobs",
        "persistence", "document_parsing", "document_generation",
        "static_site_generation", "cli_libraries", "testing_qa", "code_quality_typing",
        "runtime_validation", "developer_tools", "ai_llm", "mcp_tooling",
        "text_search", "security_auth", "media_processing", "gui_desktop",
        "native_bindings", "core_extensions",
    }
    assert expected == set(VALID_CATEGORIES)
