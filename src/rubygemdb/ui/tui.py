import os
import sys
import json
import requests
import argparse
from typing import List, Optional
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Header, Footer, DataTable, RadioSet, RadioButton, Label, Button, Markdown, ListView, ListItem
from textual.screen import ModalScreen
from textual import on, work

from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.services.context7 import Context7Service
from rubygemdb.storage.sqlite_storage import SQLiteStorage
from rubygemdb.core.config import settings
from rubygemdb.models.gem import GemEntry

class GemDetails(Vertical):
    """A widget to display detailed gem information."""
    
    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="details-container"):
            yield Label("", id="details-name", classes="details-title")
            yield Label("", id="details-classification")
            yield Label("", id="details-metadata", classes="section-content")
            yield Label("", id="details-risks", classes="section-content")
            yield Markdown("", id="details-description")
            
            yield Label("Runtime Dependencies", classes="section-title")
            yield Label("Select a dependency to fetch its cheatsheet.", classes="help-text")
            yield ListView(id="details-deps-list")
            
            with Horizontal(id="action-buttons"):
                yield Button("Fetch Cheatsheet", id="fetch-cheatsheet-btn", variant="primary")
                yield Button("Update Context7 ID", id="lookup-c7-btn", variant="warning")
            yield Button("Close", id="close-details-btn", variant="error")

    def update_gem(self, gem: GemEntry):
        self.gem = gem
        self.target_name = gem.name
        
        self.query_one("#details-name", Label).update(f"[b]{gem.name}[/b]")
        self.query_one("#details-classification", Label).update(
            f"Category: {gem.classification.primary} (Conf: {gem.classification.confidence:.2f})"
        )
        
        metadata_text = f"Source: [blue]{gem.source_code_uri or 'N/A'}[/blue]\n"
        metadata_text += f"Context7 ID: [green]{gem.context7_id or 'Missing'}[/green]"
        self.query_one("#details-metadata", Label).update(metadata_text)
        
        risk_text = (
            f"Invasiveness: {gem.risks.invasiveness}/5\n"
            f"Coupling: {gem.risks.coupling}/4\n"
            f"Leak: {gem.risks.abstraction_leak}"
        )
        self.query_one("#details-risks", Label).update(risk_text)
        
        desc = gem.description or "No description available."
        self.query_one("#details-description", Markdown).update(desc)
        
        # Populate Dependencies
        deps_list = self.query_one("#details-deps-list", ListView)
        deps_list.clear()
        
        if not gem.dependencies:
            deps_list.append(ListItem(Label("No runtime dependencies")))
        else:
            for dep in gem.dependencies:
                item = ListItem(Label(dep))
                setattr(item, "dep_name", dep)
                deps_list.append(item)
        
        # Reset button label to main gem
        self.update_button_label(self.target_name)

    def update_button_label(self, name: str):
        self.target_name = name
        self.query_one("#fetch-cheatsheet-btn", Button).label = f"Fetch Context7: {name}"

class C7SelectionScreen(ModalScreen[str]):
    """A screen to select a Context7 library ID from a list of results."""
    def __init__(self, results: list):
        super().__init__()
        self.results = results

    def compose(self) -> ComposeResult:
        with Vertical(id="selection-dialog"):
            yield Label("Select the correct Context7 Library ID:", classes="section-title")
            list_items = []
            for r in self.results:
                lib_id = r.get("id") or r.get("libraryId")
                if not lib_id:
                    continue
                desc = r.get("description", "No description")
                name = r.get("title") or r.get("name") or "Unknown"
                item = ListItem(Label(f"[b]{lib_id}[/b] - {name}\n[i]{desc[:100]}...[/i]"))
                setattr(item, "lib_id", lib_id)
                list_items.append(item)
            
            yield ListView(*list_items, id="results-list")
            yield Button("Cancel", id="cancel-btn", variant="error")

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected):
        lib_id = getattr(event.item, "lib_id", None)
        if lib_id:
            self.dismiss(lib_id)

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel(self):
        self.dismiss(None)

class GemApp(App):
    TITLE = "RubyGemDB Explorer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    CSS = """
    #sidebar {
        width: 30;
        padding: 1 2;
        background: $surface;
        border-right: vkey $background;
    }
    .section-title {
        text-style: bold;
        color: $accent;
        margin-top: 1;
        margin-bottom: 0;
    }
    .section-content {
        margin-bottom: 1;
    }
    .help-text {
        color: $text-muted;
        text-style: italic;
        margin-bottom: 1;
    }
    #details-sidebar {
        width: 50;
        padding: 1 2;
        background: $surface;
        border-left: vkey $background;
        display: none;
    }
    .details-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    #details-description {
        margin-bottom: 1;
        height: auto;
    }
    #details-deps-list {
        height: auto;
        max-height: 10;
        margin-bottom: 1;
        border: solid $background;
    }
    #fetch-cheatsheet-btn {
        margin-top: 1;
        width: 50%;
    }
    #lookup-c7-btn {
        margin-top: 1;
        width: 50%;
    }
    #close-details-btn {
        margin-top: 1;
        width: 100%;
    }
    #selection-dialog {
        padding: 2;
        background: $surface;
        border: thick $accent;
        width: 80%;
        height: 80%;
        align: center middle;
    }
    #results-list {
        margin: 1 0;
        border: solid $background;
        height: 1fr;
    }
    """

    def __init__(self, inventory_path=None):
        super().__init__()
        self.inventory_path = inventory_path
        self.rg_service = RubyGemsService()
        self.llm_service = LLMService()
        self.c7_service = Context7Service()
        self.classifier = GemClassifier(self.rg_service, self.llm_service)
        self.storage = SQLiteStorage(rubygems_service=self.rg_service, context7_service=self.c7_service)
        
        self.all_gems = []
        self.filtered_gems = []
        
        self.classifications = [
            "runtime_substrate", "framework_integration", "boundary_interface",
            "application_capability", "policy_enforcement", "observability",
            "developer_experience", "build_delivery"
        ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Filter Classification", classes="section-title")
                with RadioSet(id="class_filter"):
                    yield RadioButton("All Categories", id="cls_any", value=True)
                    for cls in self.classifications:
                        yield RadioButton(cls.replace("_", " ").title(), id=f"cls_{cls}")

            with Vertical(id="main-area"):
                self.table = DataTable(id="gems_table")
                yield self.table
            
            yield GemDetails(id="details-sidebar")
        yield Footer()

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.add_columns("Name", "Category", "Confidence", "Risks", "Context7 ID", "Source")
        
        self.load_data()

    @work(thread=True)
    def load_data(self) -> None:
        if self.inventory_path:
            self.notify(f"Populating inventory from {self.inventory_path}...")
            self.storage.load_inventory(self.inventory_path)
            
        self.all_gems = self.storage.load_classified_gems()
        self.call_from_thread(self.update_table)

    def update_table(self):
        cls_set = self.query_one("#class_filter", RadioSet)
        active_id = cls_set.pressed_button.id if cls_set.pressed_button else "cls_any"

        filtered = []
        for g in self.all_gems:
            if active_id != "cls_any":
                target_cls = active_id.replace("cls_", "")
                if g.classification.primary != target_cls:
                    continue
            filtered.append(g)

        self.filtered_gems = filtered
        self.table.clear()
        for index, gem in enumerate(filtered):
            risk_summary = f"I:{gem.risks.invasiveness} C:{gem.risks.coupling}"
            c7_id = gem.context7_id or "-"
            source = gem.source_code_uri or "-"
            self.table.add_row(
                gem.name, 
                gem.classification.primary, 
                f"{gem.classification.confidence:.2f}",
                risk_summary,
                c7_id,
                source,
                key=str(index)
            )

    @on(RadioSet.Changed)
    def on_filter_changed(self, event):
        self.update_table()

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected):
        try:
            index = int(event.row_key.value)
            gem = self.filtered_gems[index]
            sidebar = self.query_one("#details-sidebar", GemDetails)
            sidebar.update_gem(gem)
            sidebar.display = True
        except Exception as e:
            self.notify(f"Error loading details: {e}", severity="error")

    @on(ListView.Selected, "#details-deps-list")
    def on_dependency_selected(self, event: ListView.Selected):
        item = event.item
        dep_name = getattr(item, "dep_name", None)
        if not dep_name:
            return
        sidebar = self.query_one("#details-sidebar", GemDetails)
        sidebar.update_button_label(dep_name)
        self.notify(f"Targeted dependency: {dep_name}")

    @on(Button.Pressed, "#close-details-btn")
    def close_details(self) -> None:
        self.query_one("#details-sidebar").display = False

    @on(Button.Pressed, "#fetch-cheatsheet-btn")
    def trigger_fetch_cheatsheet(self) -> None:
        sidebar = self.query_one("#details-sidebar", GemDetails)
        target_name = sidebar.target_name
        self.fetch_cheatsheet(target_name)

    @on(Button.Pressed, "#lookup-c7-btn")
    def trigger_lookup_c7(self) -> None:
        sidebar = self.query_one("#details-sidebar", GemDetails)
        gem_name = sidebar.gem.name
        self.lookup_c7_id(gem_name)

    @work(thread=True)
    def lookup_c7_id(self, gem_name: str) -> None:
        self.call_from_thread(self.notify, f"Searching Context7 for {gem_name}...")
        results = self.c7_service.search_libraries(gem_name)
        
        if not results:
            self.call_from_thread(self.notify, f"No Context7 results for {gem_name}", severity="warning")
            return

        def handle_selection(selected_id: Optional[str]):
            if selected_id:
                self.storage.update_gem_metadata(gem_name, context7_id=selected_id)
                self.notify(f"Updated {gem_name} with Context7 ID: {selected_id}")
                # Refresh data and update sidebar
                self.load_data()
                # Find the updated gem and refresh sidebar
                for g in self.all_gems:
                    if g.name == gem_name:
                        g.context7_id = selected_id
                        sidebar = self.query_one("#details-sidebar", GemDetails)
                        sidebar.update_gem(g)
                        break

        self.call_from_thread(self.push_screen, C7SelectionScreen(results), handle_selection)

    @work(thread=True)
    def fetch_cheatsheet(self, target_name: str) -> None:
        if not settings.context7_api_key:
            self.call_from_thread(self.notify, "Error: context7_api_key not set in config", severity="error")
            return

        out_dir = settings.project_root / "output" / "cheatsheets"
        os.makedirs(out_dir, exist_ok=True)
        out_file = out_dir / f"{target_name}_cheatsheet.md"

        self.call_from_thread(self.notify, f"Fetching Context7 info for {target_name}...")
        
        try:
            # 1. Resolve Library ID (using service)
            lib_id = self.c7_service.verify_library(target_name)
            
            if not lib_id:
                self.call_from_thread(self.notify, f"No Context7 results found for {target_name}.", severity="warning")
                return

            # 2. Query for specific use cases
            self.call_from_thread(self.notify, f"Querying use cases for {target_name}...")
            context_query = (
                "Provide a few examples of how this gem might be used in the context of "
                "a genai application, nlp text processing, or as a systems tool."
            )
            
            context_text = self.c7_service.query_context(lib_id, context_query)
            
            if not context_text:
                self.call_from_thread(self.notify, f"Failed to get context from Context7.", severity="error")
                return

            # 3. Format and save the cheatsheet
            block = f"# {target_name} Cheatsheet\n\n"
            block += f"**Library ID**: `{lib_id}`\n\n"
            block += "## Targeted Usage Examples (GenAI / NLP / Systems)\n\n"
            block += context_text
            block += "\n\n---\n"
            block += f"\n*Generated by RubyGemDB Explorer for {target_name}*\n"
            
            with open(out_file, "w") as f:
                f.write(block)
                
            self.call_from_thread(self.notify, f"Saved cheatsheet to {out_file}")
            
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to fetch cheatsheet: {str(e)}", severity="error")

def run_tui():
    parser = argparse.ArgumentParser(description="RubyGemDB Explorer TUI")
    parser.add_argument("csv", nargs="?", help="Optional path to gems inventory CSV to populate/update DB")
    args = parser.parse_args()
    
    app = GemApp(inventory_path=args.csv)
    app.run()

if __name__ == "__main__":
    run_tui()
