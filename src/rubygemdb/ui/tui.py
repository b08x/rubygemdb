import os
import json
import threading
import argparse
from typing import Optional
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, DataTable, RadioSet, RadioButton, Label, 
    Button, Markdown, ListView, ListItem, Input, TabbedContent, Tab
)
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
            
            yield Label("Bulk Actions", classes="section-title")
            yield Button("Fetch Combined Cheatsheet", id="fetch-combined-btn", variant="primary")
            yield Button("Close", id="close-details-btn", variant="error")

    def update_gem(self, gem: GemEntry):
        self.gem = gem
        self.target_name = gem.name
        
        self.query_one("#details-name", Label).update(f"[b]{gem.name}[/b]")
        
        sub_cats = ", ".join(gem.classification.sub_categories) if gem.classification.sub_categories else "None"
        self.query_one("#details-classification", Label).update(
            f"Category: {gem.classification.primary} (Conf: {gem.classification.confidence:.2f})\nSub-cats: {sub_cats}"
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
    """A screen to select or manually enter a Context7 library ID."""
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
            
            yield Label("Or manually enter Library ID (e.g. /org/repo):", classes="section-title")
            with Horizontal(id="manual-entry-area"):
                yield Input(placeholder="/org/repo", id="manual-id-input")
                yield Button("Submit Manual", id="submit-manual-btn", variant="primary")
                
            yield Button("Cancel", id="cancel-btn", variant="error")

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected):
        lib_id = getattr(event.item, "lib_id", None)
        if lib_id:
            self.dismiss(lib_id)

    @on(Button.Pressed, "#submit-manual-btn")
    def on_submit_manual(self):
        manual_id = self.query_one("#manual-id-input", Input).value.strip()
        if manual_id:
            self.dismiss(manual_id)

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel(self):
        self.dismiss(None)

class ExportTab(Vertical):
    """Export tab with format selection and path input."""
    
    def compose(self) -> ComposeResult:
        yield Label("Export Inventory", classes="section-title")
        yield Label("Select format and destination for selected gems", classes="help-text")
        with RadioSet(id="export-format"):
            yield RadioButton("Gemfile", id="fmt-gemfile", value=True)
            yield RadioButton("CSV", id="fmt-csv")
            yield RadioButton("JSON", id="fmt-json")
            yield RadioButton("Markdown", id="fmt-md")
        with Horizontal():
            yield Input(placeholder="/path/to/export", id="export-path-input")
            yield Button("Export", id="do-export-btn", variant="primary")
        yield Label("", id="export-status-label")
    
    def set_gems(self, gem_names: list):
        self._gem_names = gem_names
        self.query_one("#export-path-input", Input).value = ""
        self.query_one("#export-status-label", Label).update("")

class GemApp(App):
    TITLE = "RubyGemDB Explorer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("space", "toggle_selection", "Toggle"),
        ("a", "select_all", "Select All"),
        ("n", "clear_selection", "Clear"),
        ("u", "bulk_update_context7", "Update C7"),
        ("e", "export_selected", "Export"),
        ("t", "switch_tab", "Switch Tab"),
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
    #fetch-combined-btn {
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
    #manual-entry-area {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }
    #manual-id-input {
        width: 1fr;
    }
    #submit-manual-btn {
        width: 20;
        margin-left: 1;
    }
    #TabbedContent {
        width: 100%;
        height: 100%;
    }
    #tab-explorer {
        width: 100%;
    }
    #tab-export {
        width: 100%;
        padding: 1 2;
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
        self._selected_gems = set()
        self._selection_lock = threading.Lock()
        self._bulk_update_queue = []
        self._bulk_update_aborted = False
        self.table: DataTable = None  # Type annotation for mypy
        
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
                with TabbedContent(id="main-tabs"):
                    with Tab("Explorer", id="tab-explorer"):
                        self.table = DataTable(id="gems_table")
                        yield self.table
                    with Tab("Export", id="tab-export"):
                        yield ExportTab(id="export-tab")
            
            yield GemDetails(id="details-sidebar")
        yield Footer()

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.add_columns("Name", "Category", "Source URI", "Context7 ID", "Description")
        
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
            desc = (gem.description[:60] + "...") if gem.description and len(gem.description) > 60 else (gem.description or "-")
            self.table.add_row(
                gem.name, 
                gem.classification.primary,
                gem.source_code_uri or "-",
                gem.context7_id or "-",
                desc,
                key=str(index)
            )

    @on(RadioSet.Changed)
    def on_filter_changed(self, event):
        self.update_table()

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected):
        try:
            row_key_value = event.row_key.value
            if row_key_value is None:
                return
            index = int(row_key_value)
            gem = self.filtered_gems[index]
            sidebar = self.query_one("#details-sidebar", GemDetails)
            sidebar.update_gem(gem)
            sidebar.display = True
        except Exception as e:
            self.notify(f"Error loading details: {e}", severity="error")

    # Multi-select actions
    def action_toggle_selection(self):
        if self.table.cursor_row is not None:
            cursor_row_value = self.table.cursor_row.value
            if cursor_row_value is None:
                return
            index = int(cursor_row_value)
            gem = self.filtered_gems[index]
            with self._selection_lock:
                if gem.name in self._selected_gems:
                    self._selected_gems.discard(gem.name)
                else:
                    self._selected_gems.add(gem.name)
                count = len(self._selected_gems)
            self._update_selection_status(count)

    def action_select_all(self):
        with self._selection_lock:
            for gem in self.filtered_gems:
                self._selected_gems.add(gem.name)
            count = len(self._selected_gems)
        self._update_selection_status(count)

    def action_clear_selection(self):
        with self._selection_lock:
            self._selected_gems.clear()
            self._bulk_update_aborted = True
            count = 0
        self._update_selection_status(count)

    def _update_selection_status(self, count: Optional[int] = None):
        if count is None:
            with self._selection_lock:
                count = len(self._selected_gems)
        self.notify(f"{count} gem{'s' if count != 1 else ''} selected")

    def action_bulk_update_context7(self):
        with self._selection_lock:
            if not self._selected_gems:
                self.notify("No gems selected", severity="warning")
                return
            self._bulk_update_queue = list(self._selected_gems)
            self._bulk_update_aborted = False
        self._process_next_context7()

    def _process_next_context7(self):
        with self._selection_lock:
            if self._bulk_update_aborted:
                self.notify("Bulk update cancelled")
                self._bulk_update_queue = []
                return
            if not self._bulk_update_queue:
                self.notify("Context7 ID update complete")
                return
            gem_name = self._bulk_update_queue.pop(0)
        
        try:
            results = self.c7_service.search_libraries(gem_name) or []
        except Exception as e:
            self.notify(f"Error searching {gem_name}: {e}", severity="error")
            self._process_next_context7()
            return
        
        if not results:
            self.notify(f"No Context7 results for {gem_name}, skipping...")
            self._process_next_context7()
            return
        
        def handle_result(selected_id):
            with self._selection_lock:
                if self._bulk_update_aborted:
                    return
            if selected_id:
                self.storage.update_gem_metadata(gem_name, context7_id=selected_id)
                for g in self.all_gems:
                    if g.name == gem_name:
                        g.context7_id = selected_id
            self._process_next_context7()
        
        self.push_screen(C7SelectionScreen(results), handle_result)

    def action_export_selected(self):
        with self._selection_lock:
            if not self._selected_gems:
                self.notify("No gems selected", severity="warning")
                return
            selected = list(self._selected_gems)
        
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = "tab-export"
        export_tab = self.query_one("#export-tab", ExportTab)
        export_tab.set_gems(selected)

    def action_switch_tab(self):
        tabs = self.query_one("#main-tabs", TabbedContent)
        current = tabs.active
        tabs.active = "tab-explorer" if current == "tab-export" else "tab-export"

    @on(Button.Pressed, "#do-export-btn")
    def on_export_pressed(self):
        export_tab = self.query_one("#export-tab", ExportTab)
        path = export_tab.query_one("#export-path-input", Input).value.strip()
        if not path:
            self.notify("Please enter an export path", severity="warning")
            return
        
        format_radios = export_tab.query_one("#export-format", RadioSet)
        format_id = format_radios.pressed_button.id if format_radios.pressed_button else "fmt-gemfile"
        
        gem_names = getattr(export_tab, "_gem_names", None) or [g.name for g in self.all_gems]
        
        missing = set(gem_names) - {g.name for g in self.all_gems}
        if missing:
            self.notify(f"Warning: {len(missing)} gems not found in inventory, skipping", severity="warning")
        
        format_map = {
            "fmt-gemfile": ("gemfile", self._export_gemfile),
            "fmt-csv": ("csv", self._export_csv),
            "fmt-json": ("json", self._export_json),
            "fmt-md": ("markdown", self._export_markdown),
        }
        
        fmt_name, export_fn = format_map.get(format_id, ("gemfile", self._export_gemfile))
        try:
            content = export_fn(gem_names)
            with open(path, "w") as f:
                f.write(content)
            export_tab.query_one("#export-status-label", Label).update(f"Exported {len(gem_names)} gems to {path}")
            self.notify(f"Exported {len(gem_names)} gems as {fmt_name}")
        except Exception as e:
            self.notify(f"Export failed: {e}", severity="error")

    def _export_gemfile(self, gem_names: list) -> str:
        lines = ["source 'https://rubygems.org'", ""]
        for name in sorted(gem_names):
            lines.append(f"gem '{name}'")
        return "\n".join(lines)

    def _export_csv(self, gem_names: list) -> str:
        output = []
        gems = [g for g in self.all_gems if g.name in gem_names]
        output.append("Name,Category,Source URI,Context7 ID,Description")
        for gem in gems:
            desc = (gem.description or "").replace('"', '""')
            output.append(f'"{gem.name}","{gem.classification.primary}","{gem.source_code_uri or ""}","{gem.context7_id or ""}","{desc}"')
        return "\n".join(output)

    def _export_json(self, gem_names: list) -> str:
        gems = [g for g in self.all_gems if g.name in gem_names]
        data = [g.model_dump() for g in gems]
        return json.dumps(data, indent=2)

    def _export_markdown(self, gem_names: list) -> str:
        gems = [g for g in self.all_gems if g.name in gem_names]
        lines = ["| Name | Category | Source | Context7 ID | Description |",
                 "|------|----------|--------|-------------|-------------|"]
        for gem in gems:
            desc = (gem.description[:50] + "...") if gem.description and len(gem.description) > 50 else (gem.description or "")
            lines.append(f"| {gem.name} | {gem.classification.primary} | {gem.source_code_uri or '-'} | {gem.context7_id or '-'} | {desc} |")
        return "\n".join(lines)

    # Existing dependency selection handler
    @on(ListView.Selected, "#details-deps-list")
    def on_dependency_selected(self, event: ListView.Selected):
        item = event.item
        dep_name = getattr(item, "dep_name", None)
        if not dep_name:
            return
        sidebar = self.query_one("#details-sidebar", GemDetails)
        sidebar.update_button_label(dep_name)
        self.notify(f"Targeted dependency: {dep_name}")

    # Button handlers
    @on(Button.Pressed, "#close-details-btn")
    def close_details(self) -> None:
        self.query_one("#details-sidebar").display = False

    @on(Button.Pressed, "#fetch-cheatsheet-btn")
    def trigger_fetch_cheatsheet(self) -> None:
        sidebar = self.query_one("#details-sidebar", GemDetails)
        target_name = sidebar.target_name
        self.fetch_cheatsheet(target_name)

    @on(Button.Pressed, "#fetch-combined-btn")
    def trigger_fetch_combined_cheatsheet(self) -> None:
        with self._selection_lock:
            if not self._selected_gems:
                self.notify("Select gems first using Space", severity="warning")
                return
            selected = list(self._selected_gems)
        self.fetch_combined_cheatsheet(selected)

    @on(Button.Pressed, "#lookup-c7-btn")
    def trigger_lookup_c7(self) -> None:
        sidebar = self.query_one("#details-sidebar", GemDetails)
        gem_name = sidebar.gem.name
        self.lookup_c7_id(gem_name)

    # Threaded workers
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
                self.load_data()
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
            lib_id = self.c7_service.verify_library(target_name)
            
            if not lib_id:
                self.call_from_thread(self.notify, f"No Context7 results found for {target_name}.", severity="warning")
                return

            self.call_from_thread(self.notify, f"Querying use cases for {target_name}...")
            context_query = (
                "Provide a few examples of how this gem might be used in the context of "
                "a genai application, nlp text processing, or as a systems tool."
            )
            
            context_text = self.c7_service.query_context(lib_id, context_query)
            
            if not context_text:
                self.call_from_thread(self.notify, "Failed to get context from Context7.", severity="error")
                return

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

    @work(thread=True)
    def fetch_combined_cheatsheet(self, gem_names: list) -> None:
        if not settings.context7_api_key:
            self.call_from_thread(self.notify, "Error: context7_api_key not set", severity="error")
            return
        
        out_dir = settings.project_root / "output" / "cheatsheets"
        os.makedirs(out_dir, exist_ok=True)
        out_file = out_dir / f"combined_cheatsheet_{len(gem_names)}_gems.md"
        
        self.call_from_thread(self.notify, f"Generating combined cheatsheet for {len(gem_names)} gems...")
        
        try:
            lib_ids = []
            for name in gem_names:
                lib_id = self.c7_service.verify_library(name)
                if lib_id:
                    lib_ids.append((name, lib_id))
            
            if not lib_ids:
                self.call_from_thread(self.notify, "No Context7 IDs found for selected gems", severity="warning")
                return
            
            blocks = []
            for name, lib_id in lib_ids:
                context = self.c7_service.query_context(lib_id,
                    "Provide 3 key usage examples for this Ruby gem in genai, nlp, or systems programming.")
                blocks.append(f"### {name}\n\nLibrary ID: `{lib_id}`\n\n{context or 'No context available.'}")
            
            from datetime import datetime
            content = f"# Combined Cheatsheet: {', '.join(gem_names)}\n\n"
            content += f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            content += f"**Gems**: {len(gem_names)}\n\n"
            content += "---\n\n".join(blocks)
            
            with open(out_file, "w") as f:
                f.write(content)
            
            self.call_from_thread(self.notify, f"Saved combined cheatsheet to {out_file}")
        
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to generate cheatsheet: {e}", severity="error")

    def action_quit(self):
        self._bulk_update_aborted = True
        super().action_quit()

def run_tui():
    parser = argparse.ArgumentParser(description="RubyGemDB Explorer TUI")
    parser.add_argument("csv", nargs="?", help="Optional path to gems inventory CSV to populate/update DB")
    args = parser.parse_args()
    
    app = GemApp(inventory_path=args.csv)
    app.run()

if __name__ == "__main__":
    run_tui()
