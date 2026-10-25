"""Stack command tests: layered manifest ordering, relevance gating, seeds, and Gemfile snippet."""
from unittest.mock import MagicMock

from rubygemdb.models.categories import LAYERS
from rubygemdb.services.stack import StackService, guess_category, seed_gems


class MockIndex:
    def __init__(self, docs):
        self.docs = docs
        self.queries = []

    def search(self, query, limit):
        self.queries.append(query)
        return self.docs[:limit]


def _mock_storage():
    storage = MagicMock()
    storage.added = []
    storage.add_gem_to_inventory.side_effect = lambda name: storage.added.append(name)
    storage.get_preferred.return_value = []
    storage.get_classified_gem.return_value = None
    storage.get_inventory_gem.return_value = None
    return storage


def test_seed_gems_ordered_layer_first():
    seeds = seed_gems()
    assert seeds, "no curated seeds found"
    layers = [s["layer"] for s in seeds]
    ranks = [LAYERS.index(ly) for ly in layers]
    assert ranks == sorted(ranks), "seeds not ordered substrate -> quality"
    all_gems = {s["gem"] for s in seeds}
    for expected in ("dotenv", "drydock", "pry", "rubocop"):
        assert expected in all_gems


def test_manifest_layers_ordered_substrate_to_quality():
    index = MockIndex([
        {"name": "sinatra", "category": "web_frameworks", "score": 0.9},
        {"name": "nokogiri", "category": "document_parsing", "score": 0.8},
        {"name": "rspec", "category": "testing_qa", "score": 0.7},
        {"name": "ffi", "category": "native_bindings", "score": 0.6},
    ])
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("CLI data pipeline tool")

    layers = [ly["layer"] for ly in manifest["layers"]]
    ranks = [LAYERS.index(ly) for ly in layers]
    assert ranks == sorted(ranks), f"layers out of order: {layers}"
    assert set(layers) == set(LAYERS), f"missing layers: {set(LAYERS) - set(layers)}"
    assert manifest["context"] == "CLI data pipeline tool"


def test_manifest_contains_base_seeds_and_matches():
    index = MockIndex([{"name": "sinatra", "category": "web_frameworks", "score": 0.9}])
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("web service")

    gems = {e["gem"]: e for layer in manifest["layers"] for e in layer["entries"]}
    # Base seed for web_frameworks is rack
    assert "rack" in gems, "base seed rack missing for web_frameworks category"
    assert gems["rack"]["source"] == "seed"
    assert "sinatra" in gems
    assert gems["sinatra"]["source"] == "match"
    assert gems["sinatra"]["category"] == "web_frameworks"

    # Irrelevant category seeds must NOT be in manifest
    for irrelevant in ("asciidoctor", "ruby-vips", "glimmer-dsl-libui"):
        assert irrelevant not in gems, f"irrelevant seed {irrelevant} leaked into manifest"


def test_irrelevant_category_seeds_excluded():
    """Verify that irrelevant categories (e.g. jekyll/asciidoctor, ruby-vips, prawn) do not enter candidate pool or stack."""
    index = MockIndex([
        {"name": "thor", "category": "cli_libraries", "score": 0.95},
        {"name": "pragmatic_segmenter", "category": "text_search", "score": 0.85},
    ])
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("tui nlp text processing")

    gems = {e["gem"] for layer in manifest["layers"] for e in layer["entries"]}
    # Relevant gems present
    assert "thor" in gems or "drydock" in gems
    assert "pragmatic_segmenter" in gems

    # Irrelevant categories should never appear
    irrelevant_seeds = {"asciidoctor", "rouge", "ruby-vips", "glimmer-dsl-libui", "bcrypt_pbkdf"}
    leaked = gems.intersection(irrelevant_seeds)
    assert not leaked, f"Irrelevant category seeds entered stack: {leaked}"


def test_max_gems_parameter_governs_density():
    """Verify --max-gems caps the maximum number of gems returned."""
    mock_docs = [
        {"name": f"gem_{i}", "category": "cli_libraries", "score": 0.9 - i * 0.05}
        for i in range(15)
    ]
    index = MockIndex(mock_docs)
    service = StackService(index=index, storage=_mock_storage())

    manifest_5 = service.build_stack("cli tooling suite", max_gems=5)
    total_5 = sum(len(ly["entries"]) for ly in manifest_5["layers"])
    assert total_5 <= 5

    manifest_8 = service.build_stack("cli tooling suite", max_gems=8)
    total_8 = sum(len(ly["entries"]) for ly in manifest_8["layers"])
    assert total_8 <= 8


def test_category_density_limit():
    """Verify that a single category is capped at at most 3 gems."""
    mock_docs = [
        {"name": f"cli_gem_{i}", "category": "cli_libraries", "score": 0.95 - i * 0.02}
        for i in range(10)
    ]
    index = MockIndex(mock_docs)
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("command line interface suite", max_gems=10)

    for layer in manifest["layers"]:
        cat_counts = {}
        for entry in layer["entries"]:
            cat = entry["category"]
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        for cat, count in cat_counts.items():
            assert count <= 3, f"Category {cat} exceeded density limit with {count} gems"


def test_contextual_role_descriptions_assigned():
    """Verify contextual task-specific roles are generated for every gem."""
    index = MockIndex([
        {"name": "thor", "category": "cli_libraries", "score": 0.9},
        {"name": "sequel", "category": "persistence", "score": 0.8},
    ])
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("CLI database migration tool")

    for layer in manifest["layers"]:
        for entry in layer["entries"]:
            role = entry.get("role")
            assert role, f"Gem {entry['gem']} has empty role"
            # Must not be the old generic static string
            assert role != "curated base pick for back-end tooling"
            assert not role.startswith("context match (score")


def test_empty_layers_omitted():
    """Verify layers without any curated gems are completely omitted from manifest and Gemfile."""
    index = MockIndex([{"name": "thor", "category": "cli_libraries", "score": 0.95}])
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("CLI command line tool")

    layers = [ly["layer"] for ly in manifest["layers"]]
    # Only composition layer should be present for pure CLI
    assert "composition" in layers
    gemfile = StackService.render_gemfile(manifest)
    assert "# composition layer" in gemfile
    # If substrate, plumbing, quality have no gems, their section headers should not appear
    for omitted_layer in [ly for ly in LAYERS if ly not in layers]:
        assert f"# {omitted_layer} layer" not in gemfile


def test_second_pass_llm_refinement_mocked():
    """Verify that when LLM service is available, its refined selection and roles are applied."""
    index = MockIndex([
        {"name": "thor", "category": "cli_libraries", "score": 0.95},
        {"name": "pastel", "category": "cli_libraries", "score": 0.85},
        {"name": "pragmatic_segmenter", "category": "text_search", "score": 0.8},
    ])
    mock_llm = MagicMock()
    mock_llm.refine_stack_selection.return_value = [
        {"gem": "thor", "role": "CLI command structure and argument dispatch"},
        {"gem": "pastel", "role": "Terminal colorizing and ANSI styling"},
        {"gem": "pragmatic_segmenter", "role": "Sentence segmentation and text boundary parsing"},
    ]

    service = StackService(index=index, storage=_mock_storage(), llm_service=mock_llm)
    manifest = service.build_stack("CLI text parser", max_gems=5)

    mock_llm.refine_stack_selection.assert_called_once()
    gems = {e["gem"]: e for ly in manifest["layers"] for e in ly["entries"]}
    assert "thor" in gems
    assert gems["thor"]["role"] == "CLI command structure and argument dispatch"
    assert gems["pastel"]["role"] == "Terminal colorizing and ANSI styling"
    assert gems["pragmatic_segmenter"]["role"] == "Sentence segmentation and text boundary parsing"


def test_missing_seed_gems_added_to_inventory():
    storage = _mock_storage()
    service = StackService(index=MockIndex([]), storage=storage)
    service.build_stack("anything")
    seeds = {s["gem"] for s in seed_gems()}
    assert seeds.issubset(set(storage.added)), "seed gems not added to inventory"


def test_gemfile_snippet_valid():
    index = MockIndex([
        {"name": "sinatra", "category": "web_frameworks", "score": 0.9},
        {"name": "rspec", "category": "testing_qa", "score": 0.85},
    ])
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("web service with testing")
    gemfile = StackService.render_gemfile(manifest)

    assert 'source "https://rubygems.org"' in gemfile
    assert 'gem "sinatra"' in gemfile
    assert 'gem "rack"' in gemfile or 'gem "rspec"' in gemfile
    assert "# composition layer" in gemfile


def test_manifest_is_serializable_yaml():
    import yaml  # type: ignore
    index = MockIndex([{"name": "pg", "category": "persistence", "score": 0.5}])
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("data tool")
    parsed = yaml.safe_load(StackService.render_manifest_yaml(manifest))
    assert parsed["context"] == "data tool"
    assert parsed["layers"]


def test_no_index_degrades_to_seeds_only(tmp_path, monkeypatch):
    monkeypatch.setattr("rubygemdb.services.stack.settings.txtai_dir", tmp_path / "nonexistent")
    service = StackService(index=None, storage=_mock_storage())
    manifest = service.build_stack("no index available")
    gems = {e["gem"] for layer in manifest["layers"] for e in layer["entries"]}
    assert "dotenv" in gems
    assert all(e["source"] == "seed" for ly in manifest["layers"] for e in ly["entries"])


def test_guess_category_falls_back_sensibly():
    assert guess_category("nokogiri") == "document_parsing"
    assert guess_category("thor") == "cli_libraries"
    assert guess_category("totally-unknown-xyzzy") == "core_extensions"
