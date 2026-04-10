import os
import json
import threading
import argparse
from typing import Optional
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, DataTable, RadioSet, RadioButton, Label,
    Button, Markdown, ListView, ListItem, Input, TabbedContent, TabPane, RichLog
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

# Tab constants for maintainable ID management
class TabConstants:
    EXPLORER = "explorer"
    EXPORT = "export"
    DEBUG = "debug"

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

class ExportTab(ScrollableContainer):
    """Export tab with format selection and path input."""
    
    def compose(self) -> ComposeResult:
        yield Label("Export Inventory", classes="section-title")
        yield Label("", id="export-summary", classes="help-text")
        yield Label("Select format and destination for selected gems", classes="help-text")
        with RadioSet(id="export-format"):
            yield RadioButton("Gemfile", id="fmt-gemfile", value=True)
            yield RadioButton("CSV", id="fmt-csv")
            yield RadioButton("JSON", id="fmt-json")
            yield RadioButton("Markdown", id="fmt-md")

        yield Label("Export Preview:", classes="section-title")
        yield Markdown("*Select gems and format to see preview*", id="export-preview")

        with Horizontal(id="export-actions"):
            yield Input(placeholder="/path/to/export", id="export-path-input")
            yield Button("Export", id="do-export-btn", variant="primary")
        yield Label("", id="export-status-label")
    
    def set_gems(self, gem_names: list):
        self._gem_names = gem_names

        # Set a helpful default export path
        if gem_names:
            default_path = f"/home/{os.environ.get('USER', 'user')}/selected_gems.gemfile"
            self.query_one("#export-path-input", Input).value = default_path
        else:
            self.query_one("#export-path-input", Input).value = ""

        self.query_one("#export-status-label", Label).update("")

        # Update summary with gem count and preview
        gem_count = len(gem_names)
        if gem_count == 0:
            summary_text = "No gems selected for export"
        elif gem_count == 1:
            summary_text = f"Ready to export 1 gem: {gem_names[0]}"
        elif gem_count <= 5:
            gems_list = ", ".join(gem_names)
            summary_text = f"Ready to export {gem_count} gems: {gems_list}"
        else:
            preview_gems = ", ".join(gem_names[:3])
            summary_text = f"Ready to export {gem_count} gems: {preview_gems}, ... and {gem_count-3} more"

        self.query_one("#export-summary", Label).update(summary_text)

        # Update export preview
        self.update_export_preview()

    def update_export_preview(self):
        """Update the export preview based on current format and gems."""
        try:
            if not hasattr(self, '_gem_names') or not self._gem_names:
                self.query_one("#export-preview", Markdown).update("*No gems selected for export*")
                return

            # Get current format
            format_radios = self.query_one("#export-format", RadioSet)
            format_id = format_radios.pressed_button.id if format_radios.pressed_button else "fmt-gemfile"

            # Generate preview content (first 10 lines only)
            preview_content = self._generate_export_preview(self._gem_names, format_id)
            self.query_one("#export-preview", Markdown).update(preview_content)

        except Exception:
            self.query_one("#export-preview", Markdown).update("*Preview unavailable*")

    def _generate_export_preview(self, gem_names: list, format_id: str) -> str:
        """Generate a preview of export content."""
        # Limit preview to first 5 gems for brevity
        preview_gems = gem_names[:5]

        if format_id == "fmt-gemfile":
            lines = ["```ruby", "source 'https://rubygems.org'", ""]
            for name in sorted(preview_gems):
                lines.append(f"gem '{name}'")
            if len(gem_names) > 5:
                lines.append(f"# ... and {len(gem_names) - 5} more gems")
            lines.append("```")

        elif format_id == "fmt-csv":
            lines = ["```csv", "Name,Category,Source URI,Context7 ID,Description"]
            for name in preview_gems:
                lines.append(f"{name},category,source_uri,context7_id,description")
            if len(gem_names) > 5:
                lines.append(f"# ... and {len(gem_names) - 5} more gems")
            lines.append("```")

        elif format_id == "fmt-json":
            lines = ["```json", "["]
            for i, name in enumerate(preview_gems):
                comma = "," if i < len(preview_gems) - 1 else ""
                lines.append(f'  {{"name": "{name}", "category": "...", "description": "..."}}{comma}')
            if len(gem_names) > 5:
                lines.append(f'  // ... and {len(gem_names) - 5} more gems')
            lines.append("]```")

        elif format_id == "fmt-md":
            lines = ["| Name | Category | Source | Context7 ID | Description |",
                    "|------|----------|--------|-------------|-------------|"]
            for name in preview_gems:
                lines.append(f"| {name} | category | source | context7_id | description |")
            if len(gem_names) > 5:
                lines.append(f"| ... and {len(gem_names) - 5} more gems | | | | |")

        return "\n".join(lines)

    @on(RadioSet.Changed, "#export-format")
    def on_format_changed(self, event):
        """Update preview when export format changes."""
        self.update_export_preview()

class GemApp(App):
    TITLE = "RubyGemDB Explorer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
        ("escape", "quit", "Quit"),
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
    #explorer {
        width: 100%;
    }
    #export {
        width: 100%;
        padding: 1 2;
    }
    #debug {
        width: 100%;
        padding: 1 2;
    }
    DataTable {
        height: 1fr;
        width: 100%;
    }
    #gems_table {
        height: 1fr;
        width: 100%;
    }
    #main-area {
        height: 1fr;
    }
    #export-preview {
        height: 15;
        margin: 1 0;
        border: solid $background;
        padding: 1;
        background: $surface;
    }
    #export-actions {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }
    #export-path-input {
        width: 1fr;
    }
    #do-export-btn {
        width: 15;
        margin-left: 1;
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
                    with TabPane("Explorer", id=TabConstants.EXPLORER):
                        self.table = DataTable(id="gems_table")
                        yield self.table
                    with TabPane("Export", id=TabConstants.EXPORT):
                        yield ExportTab(id="export-tab")
                    with TabPane("Debug", id=TabConstants.DEBUG):
                        self.debug_log = RichLog(id="debug-log", auto_scroll=True)
                        yield self.debug_log
            
            yield GemDetails(id="details-sidebar")
        yield Footer()

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.add_columns("Name", "Category", "Source URI", "Context7 ID", "Description")

        # Initialize debug logging
        self.log_info("RubyGemDB TUI initialized")
        self.log_debug(f"DataTable setup - cursor_type: {self.table.cursor_type}")

        self.load_data()

        # Ensure table can receive focus for navigation
        self.table.can_focus = True
        self.table.focus()
        self.log_debug("Table focus set after initialization")

    # Error handling and logging infrastructure
    def handle_error(self, message: str, exception: Exception = None):
        """Centralized error handling with debug logging and user notification."""
        import traceback

        error_msg = f"ERROR: {message}"
        if exception:
            error_msg += f"\nException: {exception}"
            stack_trace = traceback.format_exc()
            self.log_error(f"{error_msg}\n{stack_trace}")
        else:
            self.log_error(error_msg)

        # Show user-friendly notification
        self.notify(message, severity="error")

    def log_debug(self, message: str):
        """Log debug message to debug tab."""
        if hasattr(self, 'debug_log'):
            self.debug_log.write(f"[dim]DEBUG[/dim]: {message}")

    def log_info(self, message: str):
        """Log info message to debug tab."""
        if hasattr(self, 'debug_log'):
            self.debug_log.write(f"[blue]INFO[/blue]: {message}")

    def log_error(self, message: str):
        """Log error message to debug tab."""
        if hasattr(self, 'debug_log'):
            self.debug_log.write(f"[red]ERROR[/red]: {message}")

    def log_warning(self, message: str):
        """Log warning message to debug tab."""
        if hasattr(self, 'debug_log'):
            self.debug_log.write(f"[yellow]WARNING[/yellow]: {message}")

    @work(thread=True)
    def load_data(self) -> None:
        try:
            if self.inventory_path:
                self.call_from_thread(self.notify, f"Populating inventory from {self.inventory_path}...")
                self.storage.load_inventory(self.inventory_path)

            self.all_gems = self.storage.load_classified_gems()
            self.call_from_thread(self.log_info, f"Loaded {len(self.all_gems)} gems from storage")
            self.call_from_thread(self.update_table)
        except Exception as e:
            self.call_from_thread(self.handle_error, f"Failed to load gem data: {e}", e)

    def update_table(self):
        try:
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

            self.log_debug(f"Updating table with {len(filtered)} gems (filter: {active_id})")

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

            # Ensure table has focus and cursor for navigation
            self.table.focus()
            self.log_debug(f"Table updated successfully with {len(filtered)} rows, row count: {self.table.row_count}")

            # Log table state for debugging
            if self.table.row_count > 0:
                self.log_debug(f"Table cursor type: {self.table.cursor_type}, has focus: {self.table.has_focus}")
            else:
                self.log_error(f"Table has no rows after adding {len(filtered)} gems!")

        except Exception as e:
            self.handle_error(f"Failed to update table: {e}", e)

    @on(RadioSet.Changed)
    def on_filter_changed(self, event):
        self.update_table()

    @on(TabbedContent.TabActivated)
    def on_tab_activated(self, event: TabbedContent.TabActivated):
        """Sync selected gems when switching to export tab manually."""
        if event.pane.id == TabConstants.EXPORT:
            export_tab = self.query_one("#export-tab", ExportTab)
            with self._selection_lock:
                selected = list(self._selected_gems)
            export_tab.set_gems(selected)
            self.log_debug(f"Export tab activated, synced {len(selected)} gems")

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected):
        try:
            row_key_value = event.row_key.value if hasattr(event.row_key, 'value') else str(event.row_key)
            if row_key_value is None:
                self.log_debug("RowSelected event with None row_key")
                return
            index = int(row_key_value)
            if index >= len(self.filtered_gems):
                self.log_error(f"RowSelected index {index} out of range (max: {len(self.filtered_gems)})")
                return
            gem = self.filtered_gems[index]
            sidebar = self.query_one("#details-sidebar", GemDetails)
            sidebar.update_gem(gem)
            self.log_debug(f"Selected gem for details: {gem.name}")
            sidebar.display = True
        except Exception as e:
            self.handle_error(f"Error loading gem details: {e}", e)

    # Multi-select actions
    def action_toggle_selection(self):
        try:
            # Use coordinate_to_cell_key to safely get the current row
            if self.table.cursor_coordinate is None:
                self.log_debug("No cursor coordinate available for toggle selection")
                return

            row_key, _ = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
            if row_key is None:
                self.log_debug("No valid row key at cursor position")
                return

            index = int(row_key.value) if hasattr(row_key, 'value') else int(str(row_key))
            if index >= len(self.filtered_gems):
                self.log_error(f"Row index {index} out of range (max: {len(self.filtered_gems)})")
                return

            gem = self.filtered_gems[index]
            with self._selection_lock:
                if gem.name in self._selected_gems:
                    self._selected_gems.discard(gem.name)
                    self.log_debug(f"Deselected gem: {gem.name}")
                else:
                    self._selected_gems.add(gem.name)
                    self.log_debug(f"Selected gem: {gem.name}")
                count = len(self._selected_gems)
            self._update_selection_status(count)
        except Exception as e:
            self.handle_error(f"Toggle selection failed: {e}", e)

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

        # Defense-in-depth: Log tab switch for debugging future issues
        self.log_debug(f"Switching to export tab: {TabConstants.EXPORT}")
        tabs.active = TabConstants.EXPORT
        export_tab = self.query_one("#export-tab", ExportTab)
        export_tab.set_gems(selected)

    def action_switch_tab(self):
        tabs = self.query_one("#main-tabs", TabbedContent)
        current = tabs.active
        # Cycle through explorer → export → debug → explorer...
        if current == TabConstants.EXPLORER:
            tabs.active = TabConstants.EXPORT
        elif current == TabConstants.EXPORT:
            tabs.active = TabConstants.DEBUG
        else:
            tabs.active = TabConstants.EXPLORER

    @on(Button.Pressed, "#do-export-btn")
    def on_export_pressed(self):
        export_tab = self.query_one("#export-tab", ExportTab)

        # Check if any gems are selected
        gem_names = getattr(export_tab, "_gem_names", None)
        if not gem_names:
            self.notify("No gems selected! Go to Explorer tab, select gems with Space key, then return here.", severity="error")
            export_tab.query_one("#export-status-label", Label).update("❌ No gems selected for export")
            return

        path = export_tab.query_one("#export-path-input", Input).value.strip()
        if not path:
            self.notify("Please enter an export file path (e.g., /home/user/my_gems.gemfile)", severity="warning")
            export_tab.query_one("#export-status-label", Label).update("❌ Please enter export path")
            return

        # Log export attempt for debugging
        self.log_info(f"Attempting to export {len(gem_names)} gems to {path}")
        
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

            # Show success status
            export_tab.query_one("#export-status-label", Label).update(f"✅ Successfully exported {len(gem_names)} gems to {path}")
            self.notify(f"✅ Exported {len(gem_names)} gems as {fmt_name}", timeout=3.0)
            self.log_info(f"Export successful: {len(gem_names)} gems saved to {path}")

        except Exception as e:
            export_tab.query_one("#export-status-label", Label).update(f"❌ Export failed: {e}")
            self.notify(f"❌ Export failed: {e}", severity="error")
            self.log_error(f"Export failed: {e}")

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

    def action_refresh(self):
        """Refresh the gem data from storage."""
        try:
            self.log_info("Refreshing gem data...")
            self.load_data()
        except Exception as e:
            self.handle_error(f"Failed to refresh data: {e}", e)

    def action_quit(self):
        """Gracefully quit the application."""
        try:
            self.log_info("User requested quit - shutting down gracefully")
            self._bulk_update_aborted = True
            self.exit()
        except Exception as e:
            self.log_error(f"Error during quit: {e}")
            # Force exit if there's an issue
            self.exit(1)

def run_tui():
    parser = argparse.ArgumentParser(description="RubyGemDB Explorer TUI")
    parser.add_argument("csv", nargs="?", help="Optional path to gems inventory CSV to populate/update DB")
    args = parser.parse_args()
    
    app = GemApp(inventory_path=args.csv)
    app.run()

if __name__ == "__main__":
    run_tui()
