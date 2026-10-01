"""Stack command tests: layered manifest ordering, seeds, Gemfile snippet."""
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
    for seed in ("dotenv", "pry", "rubocop"):
        assert seed in gems, f"base seed {seed} missing from manifest"
        assert gems[seed]["source"] == "seed"
    assert "sinatra" in gems
    assert gems["sinatra"]["source"] == "match"
    assert gems["sinatra"]["category"] == "web_frameworks"


def test_missing_seed_gems_added_to_inventory():
    storage = _mock_storage()
    service = StackService(index=MockIndex([]), storage=storage)
    service.build_stack("anything")
    seeds = {s["gem"] for s in seed_gems()}
    assert seeds.issubset(set(storage.added)), "seed gems not added to inventory"


def test_gemfile_snippet_valid():
    index = MockIndex([{"name": "sinatra", "category": "web_frameworks", "score": 0.9}])
    service = StackService(index=index, storage=_mock_storage())
    manifest = service.build_stack("web service")
    gemfile = StackService.render_gemfile(manifest)

    assert 'source "https://rubygems.org"' in gemfile
    assert 'gem "dotenv"' in gemfile
    assert 'gem "sinatra"' in gemfile
    assert "# substrate layer" in gemfile
    assert "# quality layer" in gemfile


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
