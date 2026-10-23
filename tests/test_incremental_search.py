"""Incremental search tests: find-as-you-type over the gem table and the
AddGemScreen picker, plus the rubygems.org search backing it."""
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Label, ListView

from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.ui.tui import AddGemScreen

EXISTING_GEMS = ["nokogiri", "sidekiq", "sinatra", "rails"]


class _ScreenHost(App):
    """Minimal host so AddGemScreen can run under a pilot."""

    def compose(self) -> ComposeResult:
        yield Label("host")


def _suggestion_names(screen: AddGemScreen) -> list:
    """Names carried by the suggestion ListItems (skips hint rows)."""
    suggestions = screen.query_one("#gem-suggestions", ListView)
    return [n for n in (getattr(item, "gem_name", None) for item in suggestions.children) if n]


@pytest.mark.asyncio
async def test_add_gem_screen_filters_local_matches_as_you_type():
    app = _ScreenHost()
    async with app.run_test() as pilot:
        screen = AddGemScreen(EXISTING_GEMS)
        app.push_screen(screen)
        await pilot.pause()

        search = screen.query_one("#gem-name-input", Input)
        search.focus()
        await pilot.press(*"si")
        await pilot.pause()

        # Every keystroke refilters: "si" matches sidekiq and sinatra
        assert _suggestion_names(screen) == ["sidekiq", "sinatra"]

        await pilot.press(*"de")  # now "sidekiq"
        await pilot.pause()
        assert _suggestion_names(screen) == ["sidekiq"]

        # Clearing the query clears the matches
        search.clear()
        await pilot.pause()
        assert _suggestion_names(screen) == []


@pytest.mark.asyncio
async def test_add_gem_screen_enter_adds_typed_name():
    app = _ScreenHost()
    results: list = []
    async with app.run_test() as pilot:
        screen = AddGemScreen(EXISTING_GEMS)
        app.push_screen(screen, results.append)
        await pilot.pause()

        search = screen.query_one("#gem-name-input", Input)
        search.focus()
        await pilot.press(*"redis")
        await pilot.press("enter")

    assert results == ["redis"]


@pytest.mark.asyncio
async def test_add_gem_screen_pick_suggestion_dismisses_with_name():
    app = _ScreenHost()
    results: list = []
    async with app.run_test() as pilot:
        screen = AddGemScreen(EXISTING_GEMS)
        app.push_screen(screen, results.append)
        await pilot.pause()

        search = screen.query_one("#gem-name-input", Input)
        search.focus()
        await pilot.press(*"sin")
        await pilot.pause()

        suggestions = screen.query_one("#gem-suggestions", ListView)
        suggestions.focus()
        suggestions.index = 0
        await pilot.pause()
        await pilot.press("enter")

    assert results == ["sinatra"]


def test_search_gems_maps_and_truncates(monkeypatch):
    service = RubyGemsService()
    monkeypatch.setattr(service, "cache", {})
    monkeypatch.setattr(service, "_save_cache", lambda: None)

    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = [
        {"name": "sidekiq", "info": "Simple job processing"},
        {"info": "nameless entries are skipped"},
        {"name": "gush", "info": None},
        {"name": "extra", "info": "beyond the limit"},
    ]
    monkeypatch.setattr("rubygemdb.services.rubygems.requests.get", lambda *a, **k: fake)

    assert service.search_gems("job", limit=2) == [
        {"name": "sidekiq", "info": "Simple job processing"},
        {"name": "gush", "info": ""},
    ]
    # Cached per query so repeated keystrokes don't re-hit the API
    assert "__search__job" in service.cache


def test_search_gems_quotes_query_and_handles_empty(monkeypatch):
    service = RubyGemsService()
    monkeypatch.setattr(service, "cache", {})
    monkeypatch.setattr(service, "_save_cache", lambda: None)

    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = [{"name": "pdf-reader", "info": "x"}]
        return fake

    monkeypatch.setattr("rubygemdb.services.rubygems.requests.get", fake_get)

    assert service.search_gems("") == []

    results = service.search_gems("pdf reader")
    assert results == [{"name": "pdf-reader", "info": "x"}]
    assert seen["url"].endswith("query=pdf+reader")
