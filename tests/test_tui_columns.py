"""Tests for expandable columns and form-fitting text in the TUI details pane."""
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from rubygemdb.models.gem import GemEntry, GemClassification, GemRisks, GemSignals
from rubygemdb.ui.tui import GemDetails


def create_gem(name: str, source: str | None = None, c7_id: str | None = None) -> GemEntry:
    return GemEntry(
        name=name,
        classification=GemClassification(primary="cli_libraries", sub_categories=["cli_frameworks"]),
        description="A sample gem description for testing expandable columns.",
        source_code_uri=source,
        context7_id=c7_id,
        dependencies=["rake"],
        role={"description": "Test role"},
        capabilities=[],
        risks=GemRisks(),
        signals=GemSignals(),
    )


class _DetailsHost(App):
    """Host app to test GemDetails widget in isolation under a pilot."""
    CSS = """
    #details-sidebar {
        width: 50;
        min-width: 46;
        max-width: 96;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield GemDetails(id="details-sidebar")


def test_form_fit_width_calculation_long_and_short_urls():
    details = GemDetails(id="details-sidebar")

    # Long repository URL (such as PopulateTools/ansible_doc_generator)
    long_gem = create_gem(
        name="ansible_doc_generator",
        source="https://github.com/PopulateTools/ansible_doc_generator",
        c7_id="/populatetools/ansible-doc-generator",
    )
    long_width = details.calculate_form_fit_width(long_gem)
    # The source line is 63 chars long, so form fit must be at least 70
    assert long_width >= 70

    # Short repository URL
    short_gem = create_gem(
        name="rake",
        source="https://github.com/ruby/rake",
        c7_id="/ruby/rake",
    )
    short_width = details.calculate_form_fit_width(short_gem)
    # Short gem should narrow down to the minimum comfortable button width (46-48)
    assert 46 <= short_width < long_width


@pytest.mark.asyncio
async def test_gem_details_update_adjusts_width_and_toggles_expand():
    app = _DetailsHost()
    async with app.run_test(size=(140, 40)) as pilot:
        details = app.query_one("#details-sidebar", GemDetails)

        long_gem = create_gem(
            name="ansible_doc_generator",
            source="https://github.com/PopulateTools/ansible_doc_generator",
            c7_id="/populatetools/ansible-doc-generator",
        )

        details.update_gem(long_gem)
        await pilot.pause()

        # Check form-fit width was applied
        initial_width = details.styles.width.value if details.styles.width else 0
        assert initial_width >= 70

        # Check toggle width button exists and is in "Expand (w)" state
        btn = details.query_one("#toggle-width-btn", Button)
        assert btn.label.plain == "Expand (w)"

        # Trigger toggle expand
        details.toggle_expand()
        await pilot.pause()

        expanded_width = details.styles.width.value if details.styles.width else 0
        assert expanded_width >= initial_width
        assert btn.label.plain == "Narrow (w)"

        # Toggle back to form fit
        details.toggle_expand()
        await pilot.pause()

        restored_width = details.styles.width.value if details.styles.width else 0
        assert restored_width == initial_width
        assert btn.label.plain == "Expand (w)"


@pytest.mark.asyncio
async def test_button_press_toggles_expansion():
    app = _DetailsHost()
    async with app.run_test(size=(140, 40)) as pilot:
        details = app.query_one("#details-sidebar", GemDetails)
        gem = create_gem("test_gem", "https://github.com/example/test_gem")
        details.update_gem(gem)
        await pilot.pause()

        assert not details.is_expanded

        # Press the expand button
        btn = details.query_one("#toggle-width-btn", Button)
        btn.press()
        await pilot.pause()

        assert details.is_expanded
        assert btn.label.plain == "Narrow (w)"

        # Press again to narrow
        btn.press()
        await pilot.pause()

        assert not details.is_expanded
        assert btn.label.plain == "Expand (w)"


def test_gemapp_sidebar_and_details_actions():
    from unittest.mock import MagicMock
    from rubygemdb.ui.tui import GemApp

    app = GemApp.__new__(GemApp)
    app.log_debug = MagicMock()

    sidebar = MagicMock()
    sidebar.display = True
    details = MagicMock()
    details.display = True

    def mock_query_one(selector, *args, **kwargs):
        if selector == "#sidebar":
            return sidebar
        elif selector == "#details-sidebar":
            return details
        raise ValueError(f"Unknown selector: {selector}")

    app.query_one = mock_query_one

    # Test toggle sidebar
    app.action_toggle_sidebar()
    assert sidebar.display is False
    app.action_toggle_sidebar()
    assert sidebar.display is True

    # Test toggle details expand
    app.action_toggle_details_expand()
    details.toggle_expand.assert_called_once()

