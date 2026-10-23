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


def test_every_category_has_subcategories():
    for c in CATEGORIES:
        assert c.sub_categories, f"{c.slug} has no sub_categories defined"
        assert len(c.sub_categories) >= 2, f"{c.slug} has fewer than 2 sub_categories"
        for sub_slug, examples in c.sub_categories.items():
            assert sub_slug.islower(), f"{c.slug} sub-category {sub_slug} not lowercase"
            assert "_" in sub_slug or sub_slug.isalnum(), f"{c.slug} sub-category {sub_slug} invalid format"
            assert len(examples) >= 1, f"{c.slug} sub-category {sub_slug} has no example gems"
            for gem in examples:
                assert isinstance(gem, str) and gem, f"Invalid gem in {c.slug}.{sub_slug}: {gem}"


def test_subcategories_vocabulary_coverage():
    expected_subs = {
        "web_frameworks": ("full_stack_frameworks", "micro_frameworks", "rack_middleware", "templating_engines", "frontend_assets"),
        "http_networking": ("http_clients", "api_sdks", "networking_protocols", "remote_storage_transfer"),
        "servers_concurrency": ("application_servers", "async_evented", "concurrency_parallelism", "process_supervision"),
        "background_jobs": ("job_queues", "workflow_dag_orchestration", "job_scheduling"),
        "persistence": ("relational_orms", "database_drivers", "key_value_caches", "file_attachment_storage", "specialized_datastores"),
        "document_parsing": ("html_xml_parsing", "json_yaml_serialization", "csv_tabular_formats", "markdown_prose_parsing", "code_ast_parsers", "structured_extractors", "archive_compression"),
        "document_generation": ("pdf_generation", "api_doc_generators", "syntax_highlighting"),
        "static_site_generation": ("site_generators", "jekyll_plugins", "site_themes"),
        "testing_qa": ("test_frameworks", "mocking_stubbing", "fixtures_factories", "browser_integration", "test_tooling_reporting"),
        "code_quality_typing": ("linters_formatters", "rubocop_extensions", "type_checkers", "language_servers", "security_static_analysis"),
        "runtime_validation": ("schema_validation", "type_containers_structs", "object_mapping_attributes"),
        "developer_tools": ("debugging_repl", "profiling_benchmarks", "logging_telemetry", "development_workflow"),
        "mcp_tooling": ("mcp_servers", "mcp_clients"),
        "text_search": ("nlp_tokenization", "string_similarity_fuzzy", "search_ranking_retrieval", "lexical_knowledge_bases"),
        "security_auth": ("authentication_oauth", "authorization_policies", "cryptography_encryption"),
        "media_processing": ("image_processing", "audio_music_processing", "video_transcoding"),
        "gui_desktop": ("desktop_gui", "game_graphics_2d"),
        "native_bindings": ("ffi_interop", "compilation_packaging", "binary_executable_tools"),
        "core_extensions": ("language_core_extensions", "data_structures_collections", "environment_system_utilities", "data_standards_reference"),
    }
    for slug, subs in expected_subs.items():
        cat_subs = CATEGORY_BY_SLUG[slug].sub_categories
        for sub in subs:
            assert sub in cat_subs, f"{slug} missing expected sub-category {sub}"


def test_default_invasiveness_and_leak_within_valid_ranges():
    valid_leaks = {"low", "medium", "high"}
    for c in CATEGORIES:
        assert 1 <= c.default_invasiveness <= 5, f"{c.slug} invasiveness {c.default_invasiveness} out of bounds"
        assert c.default_abstraction_leak in valid_leaks, f"{c.slug} invalid leak {c.default_abstraction_leak}"

