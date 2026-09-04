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
from rubygemdb.services.classifier import GemClassifier, VALID_CATEGORIES
from rubygemdb.services.context7 import Context7Service
from rubygemdb.storage.sqlite_storage import SQLiteStorage
from rubygemdb.core.config import settings
from rubygemdb.models.gem import GemEntry
from rubygemdb.agent import TxtaiAgent

# Tab constants for maintainable ID management
class TabConstants:
    EXPLORER = "explorer"
    CHAT = "chat"
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
            
            yield Label("Gem Management", classes="section-title")
            with Horizontal(id="management-buttons"):
                yield Button("Edit Category", id="edit-gem-btn", variant="primary")
                yield Button("Remove Gem", id="remove-gem-btn", variant="error")
            
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

class AddGemScreen(ModalScreen[str]):
    """A screen to manually add a gem by name."""
    def compose(self) -> ComposeResult:
        with Vertical(id="add-gem-dialog", classes="modal-dialog"):
            yield Label("Add New Gem", classes="section-title")
            yield Label("Enter the name of the Ruby gem to add:", classes="help-text")
            yield Input(placeholder="gem-name", id="gem-name-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("Add Gem", id="add-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn", variant="error")

    @on(Button.Pressed, "#add-btn")
    def on_add(self):
        name = self.query_one("#gem-name-input", Input).value.strip()
        if name:
            self.dismiss(name)

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel(self):
        self.dismiss(None)

class EditGemScreen(ModalScreen[tuple]):
    """A screen to manually edit a gem's metadata."""
    def __init__(self, gem: GemEntry):
        super().__init__()
        self.gem = gem

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-gem-dialog", classes="modal-dialog"):
            yield Label(f"Edit Gem: [b]{self.gem.name}[/b]", classes="section-title")
            
            yield Label("Primary Category", classes="section-title")
            with RadioSet(id="category-selection"):
                for cat in VALID_CATEGORIES:
                    yield RadioButton(cat.replace("_", " ").title(), id=f"edit_cls_{cat}", value=(cat == self.gem.classification.primary))
            
            yield Label("Source Code URI", classes="section-title")
            yield Input(value=self.gem.source_code_uri or "", placeholder="https://github.com/...", id="source-uri-input")

            yield Label("Context7 ID", classes="section-title")
            yield Input(value=self.gem.context7_id or "", placeholder="/org/repo", id="c7-id-input")
            
            with Horizontal(id="dialog-buttons"):
                yield Button("Save Changes", id="save-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn", variant="error")

    @on(Button.Pressed, "#save-btn")
    def on_save(self):
        rs = self.query_one("#category-selection", RadioSet)
        selected_cat = rs.pressed_button.id.replace("edit_cls_", "") if rs.pressed_button else self.gem.classification.primary
        source_uri = self.query_one("#source-uri-input", Input).value.strip() or None
        c7_id = self.query_one("#c7-id-input", Input).value.strip() or None
        self.dismiss((selected_cat, source_uri, c7_id))

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel(self):
        self.dismiss(None)

class ConfirmDeleteScreen(ModalScreen[bool]):
    """A screen to confirm gem deletion."""
    def __init__(self, gem_name: str):
        super().__init__()
        self.gem_name = gem_name

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-delete-dialog", classes="modal-dialog"):
            yield Label("Confirm Deletion", classes="section-title")
            yield Label(f"Are you sure you want to remove [b]{self.gem_name}[/b] from the database?", classes="help-text")
            yield Label("This will remove both inventory metadata and classification results.", classes="help-text")
            
            with Horizontal(id="dialog-buttons"):
                yield Button("Delete Forever", id="delete-btn", variant="error")
                yield Button("Keep Gem", id="cancel-btn", variant="primary")

    @on(Button.Pressed, "#delete-btn")
    def on_delete(self):
        self.dismiss(True)

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel(self):
        self.dismiss(False)

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


class AgentChatTab(ScrollableContainer):
    """A chat tab for interacting with the TxtaiAgent."""

    def __init__(self, agent_loader_callback):
        super().__init__()
        self.agent_loader_callback = agent_loader_callback
        self.agent = None
        self._chat_messages: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-container"):
            yield Label("RubyGemDB Agent (Txtai/Ollama)", id="chat-title")
            yield RichLog(id="chat-log", markup=True, wrap=True)
            yield Input(placeholder="Ask the agent a question...", id="chat-input")
            with Horizontal(id="chat-actions"):
                yield Button("Export to Markdown", id="export-chat-btn", variant="primary")
                yield Button("Clear Chat", id="clear-chat-btn", variant="error")
            

    def _add_message(self, role: str, content: str) -> None:
        """Track a message for later export and display it in the RichLog."""
        from datetime import datetime
        self._chat_messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        color_map = {
            "agent": "green",
            "user": "blue",
            "system": "yellow",
            "error": "red",
        }
        color = color_map.get(role, "white")
        self.log_widget.write(f"\n[bold {color}]{role.title()}:[/bold {color}] {content}")

    def on_mount(self) -> None:
        self.log_widget = self.query_one("#chat-log", RichLog)
        self._add_message("agent", "System ready. Loading embeddings index...")
        self.load_agent()

    @work(thread=True)
    def load_agent(self):
        try:
            self.agent = self.agent_loader_callback()
            self.app.call_from_thread(self._add_message, "agent", "Ready! How can I help you today?")
        except Exception as e:
            self.app.call_from_thread(self._add_message, "error", f"Failed to load agent: {e}")

    @on(Input.Submitted, "#chat-input")
    def submit_query(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return

        event.input.value = ""
        self._add_message("user", query)

        if not self.agent:
            self._add_message("error", "Still loading... please wait.")
            return

        self.process_query(query)

    @work(thread=True)
    def process_query(self, query: str) -> None:
        self.app.call_from_thread(self.log_widget.write, "[dim]Agent is thinking...[/dim]")
        try:
            response = self.agent.run(query)
            self.app.call_from_thread(self._add_message, "agent", str(response))
        except Exception as e:
            self.app.call_from_thread(self._add_message, "error", str(e))

    def _export_chat_to_markdown(self) -> None:
        """Export the chat conversation to a markdown file."""
        if not self._chat_messages:
            self.notify("No messages to export", severity="warning")
            return

        from datetime import datetime
        out_dir = settings.project_root / "output" / "chat_exports"
        os.makedirs(out_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = out_dir / f"chat_{timestamp}.md"

        lines = [
            "# RubyGemDB Agent Chat Export",
            "",
            f"**Exported**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Messages**: {len(self._chat_messages)}",
            "",
            "---",
            "",
        ]

        for msg in self._chat_messages:
            role = msg["role"]
            content = msg["content"]
            ts = msg["timestamp"]

            if role == "user":
                lines.append("## User")
                lines.append(f"*{ts}*")
                lines.append("")
                lines.append(content)
            elif role == "agent":
                lines.append("## Agent")
                lines.append(f"*{ts}*")
                lines.append("")
                lines.append(content)
            else:
                heading = "Error" if role == "error" else "System"
                lines.append(f"### {heading}")
                lines.append(f"*{ts}*")
                lines.append("")
                lines.append(f"> {content}")

            lines.append("")
            lines.append("---")
            lines.append("")

        with open(filepath, "w") as f:
            f.write("\n".join(lines))

        self.notify(f"Chat exported to {filepath}")
        self.log_widget.write(f"\n[dim]Chat exported to {filepath}[/dim]")

    @on(Button.Pressed, "#export-chat-btn")
    def on_export_chat(self) -> None:
        self._export_chat_to_markdown()


    @on(Button.Pressed, "#clear-chat-btn")
    def on_clear_chat(self) -> None:
        self._chat_messages.clear()
        self.log_widget.clear()
        self._add_message("agent", "System ready. Loading embeddings index...")
        self.load_agent()




class GemApp(App):
    TITLE = "RubyGemDB Explorer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("c", "open_chat", "Agent Chat"),
        ("space", "toggle_selection", "Toggle"),
        ("a", "select_all", "Select All"),
        ("n", "clear_selection", "Clear"),
        ("u", "bulk_update_context7", "Update C7"),
        ("x", "export_selected", "Export"),
        ("e", "edit_gem", "Edit"),
        ("d", "delete_gem", "Delete"),
        ("plus", "add_gem", "Add"),
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
    #management-buttons {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }
    #edit-gem-btn {
        width: 50%;
    }
    #remove-gem-btn {
        width: 50%;
    }
    #fetch-combined-btn {
        margin-top: 1;
        width: 100%;
    }
    .modal-dialog {
        padding: 2;
        background: $surface;
        border: thick $accent;
        width: 60;
        height: auto;
        align: center middle;
    }
    #selection-dialog {
        padding: 2;
        background: $surface;
        border: thick $accent;
        width: 80%;
        height: 80%;
        align: center middle;
    }
    #dialog-buttons {
        height: auto;
        margin-top: 1;
    }
    #dialog-buttons Button {
        width: 50%;
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
    #category-selection {
        height: auto;
        max-height: 15;
        border: solid $background;
        margin: 1 0;
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
    #chat-container {
        padding: 1 2;
        height: 100%;
    }
    #chat-log {
        height: 1fr;
        border: solid $background;
        margin-bottom: 1;
    }
    #chat-input {
        margin-bottom: 1;
    }
    #chat-actions {
        height: auto;
        margin-bottom: 1;
    }
    #chat-actions Button {
        width: 50%;
    }
    #chat-hint {
        color: $text-muted;
        text-style: italic;
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
        self._inventory_loaded = False
        self.table: DataTable = None  # Type annotation for mypy
        
        self.classifications = list(VALID_CATEGORIES)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Filter Classification", classes="section-title")
                with RadioSet(id="class_filter"):
                    yield RadioButton("All Categories", id="cls_any", value=True)
                    for cls in self.classifications:
                        yield RadioButton(cls.replace("_", " ").title(), id=f"cls_{cls}")
                
                yield Label("System Stats", classes="section-title")
                yield Label("Total Gems: 0", id="gem-count-label")
                yield Label("Last Update: Never", id="last-update-label")

            with Vertical(id="main-area"):
                with TabbedContent(id="main-tabs"):
                    with TabPane("Explorer", id=TabConstants.EXPLORER):
                        self.table = DataTable(id="gems_table")
                        yield self.table
                    with TabPane("Chat", id=TabConstants.CHAT):
                        yield AgentChatTab(self.get_txtai_agent)
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
    def handle_error(self, message: str, exception=None):
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

    @work(thread=True, exclusive=True)
    def load_data(self) -> None:
        """Load gem data from storage and classify any new gems."""
        try:
            # Phase 1: Immediate Load from DB
            self.call_from_thread(self.log_debug, "Phase 1: Loading existing gems from DB...")
            self.all_gems = self.storage.load_classified_gems()
            self.call_from_thread(self.log_info, f"Initially loaded {len(self.all_gems)} gems from DB")
            self.call_from_thread(self.update_table)

            # Phase 2: Inventory Sync (only if path provided and not loaded yet)
            if self.inventory_path and not self._inventory_loaded:
                self.call_from_thread(self.log_info, f"Phase 2: Syncing inventory from {self.inventory_path}...")
                self.call_from_thread(self.notify, f"Populating inventory from {self.inventory_path}...")
                # Note: this can be slow. If user adds a gem while this is running, 
                # load_data will restart because it's exclusive. 
                # This is why we have add_gem_worker for manual additions.
                self.storage.load_inventory(self.inventory_path)
                self._inventory_loaded = True
                
                # Refresh all_gems after sync
                self.all_gems = self.storage.load_classified_gems()
                self.call_from_thread(self.update_table)

            # Phase 3: Classification of unclassified gems
            import sqlite3
            with sqlite3.connect(self.storage.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name, category, homepage, source_code_uri, context7_id FROM inventory WHERE name NOT IN (SELECT name FROM classified_gems)")
                unclassified = cursor.fetchall()

            if unclassified:
                total = len(unclassified)
                self.call_from_thread(self.log_info, f"Phase 3: Found {total} unclassified gems in inventory")
                self.call_from_thread(self.notify, f"Classifying {total} gems in background...")
                
                results = []
                for idx, (name, cat, hp, sc, c7) in enumerate(unclassified):
                    # Periodically update table for visibility
                    if idx > 0 and idx % 10 == 0:
                        self.call_from_thread(self.log_debug, f"Classification progress: {idx}/{total}")
                    
                    try:
                        gem_entry = self.classifier.classify(name, cat, homepage=hp, source_code_uri=sc, context7_id=c7)
                        results.append(gem_entry)
                        
                        # Batch save and update UI
                        if len(results) >= 5:
                            self.storage.save_classified_gems(results)
                            results = []
                            self.all_gems = self.storage.load_classified_gems()
                            self.call_from_thread(self.update_table)
                    except Exception as ce:
                        self.call_from_thread(self.log_error, f"Failed to classify {name}: {ce}")
                
                if results:
                    self.storage.save_classified_gems(results)
                
                self.all_gems = self.storage.load_classified_gems()
                self.call_from_thread(self.update_table)
                self.call_from_thread(self.log_info, f"Background classification complete. Total gems: {len(self.all_gems)}")

        except Exception as e:
            self.call_from_thread(self.handle_error, f"Failed background load_data: {e}", e)

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

            # Sort alphabetically by name
            filtered.sort(key=lambda x: x.name.lower())

            self.filtered_gems = filtered
            self.table.clear()

            # Update stats labels
            from datetime import datetime
            self.query_one("#gem-count-label", Label).update(f"Total Gems: {len(self.all_gems)}")
            self.query_one("#last-update-label", Label).update(f"Last Update: {datetime.now().strftime('%H:%M:%S')}")

            self.log_debug(f"Updating table with {len(filtered)} filtered gems out of {len(self.all_gems)} total")

            for index, gem in enumerate(filtered):
                desc = (gem.description[:60] + "...") if gem.description and len(gem.description) > 60 else (gem.description or "-")
                self.table.add_row(
                    gem.name,
                    gem.classification.primary,
                    gem.source_code_uri or "-",
                    gem.context7_id or "-",
                    desc,
                    key=gem.name
                )

            if len(filtered) == 0 and len(self.all_gems) > 0:
                self.log_warning(f"Filter '{active_id}' resulted in 0 gems, but {len(self.all_gems)} total gems are loaded.")

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
            gem_name = event.row_key.value if hasattr(event.row_key, 'value') else str(event.row_key)
            if gem_name is None:
                self.log_debug("RowSelected event with None row_key")
                return
            
            gem = next((g for g in self.filtered_gems if g.name == gem_name), None)
            if not gem:
                self.log_error(f"RowSelected: gem '{gem_name}' not found in filtered list")
                return
            
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

            gem_name = row_key.value if hasattr(row_key, 'value') else str(row_key)
            with self._selection_lock:
                if gem_name in self._selected_gems:
                    self._selected_gems.discard(gem_name)
                    self.log_debug(f"Deselected gem: {gem_name}")
                else:
                    self._selected_gems.add(gem_name)
                    self.log_debug(f"Selected gem: {gem_name}")
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
            tabs.active = TabConstants.CHAT
        elif current == TabConstants.CHAT:
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

    @on(Button.Pressed, "#edit-gem-btn")
    def trigger_edit_gem(self) -> None:
        self.action_edit_gem()

    @on(Button.Pressed, "#remove-gem-btn")
    def trigger_remove_gem(self) -> None:
        self.action_delete_gem()

    def action_add_gem(self):
        def handle_add(name: Optional[str]):
            if name:
                self.log_info(f"User manually adding gem: {name}")
                self.notify(f"Adding {name}...")
                self.add_gem_worker(name)
        
        self.push_screen(AddGemScreen(), handle_add)

    @work(thread=True)
    def add_gem_worker(self, name: str):
        """Worker to add and classify a single gem immediately."""
        try:
            self.call_from_thread(self.log_debug, f"Adding {name} to inventory storage...")
            self.storage.add_gem_to_inventory(name)
            
            self.call_from_thread(self.log_debug, f"Performing immediate classification for {name}...")
            # We don't have cat/hp/sc/c7 for manual addition yet, classify will fetch from RubyGems
            gem_entry = self.classifier.classify(name)
            
            self.call_from_thread(self.log_debug, f"Saving classification for {name} to DB...")
            self.storage.save_classified_gems([gem_entry])
            
            self.call_from_thread(self.log_info, f"Successfully added and classified manual gem: {name}")
            self.call_from_thread(self.notify, f"Successfully added and classified {name}")
            
            # Update local state directly for immediate UI feedback
            found = False
            for i, existing in enumerate(self.all_gems):
                if existing.name == name:
                    self.all_gems[i] = gem_entry
                    found = True
                    break
            if not found:
                self.all_gems.append(gem_entry)
            
            # Refresh display immediately
            self.call_from_thread(self.update_table)
            
        except Exception as e:
            self.call_from_thread(self.handle_error, f"Failed to add manual gem {name}: {e}", e)

    def action_edit_gem(self):
        sidebar = self.query_one("#details-sidebar", GemDetails)
        if not sidebar.display or not hasattr(sidebar, 'gem'):
            # Try to get from table selection if sidebar is closed
            try:
                row_key, _ = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
                gem_name = row_key.value if hasattr(row_key, 'value') else str(row_key)
                gem = next((g for g in self.filtered_gems if g.name == gem_name), None)
                if not gem:
                    raise ValueError(f"Gem '{gem_name}' not found")
            except Exception:
                self.notify("Select a gem to edit", severity="warning")
                return
        else:
            gem = sidebar.gem

        def handle_edit(result: Optional[tuple]):
            if result:
                new_category, new_source_uri, new_c7_id = result
                self.log_info(f"Updating gem {gem.name}: category={new_category}, source_uri={new_source_uri}, c7_id={new_c7_id}")
                
                # Persistence
                self.storage.update_gem_classification(gem.name, new_category)
                self.storage.update_gem_metadata(gem.name, source_code_uri=new_source_uri, context7_id=new_c7_id)
                
                self.notify(f"Updated {gem.name}")
                
                # Update local state
                for g in self.all_gems:
                    if g.name == gem.name:
                        g.classification.primary = new_category
                        g.classification.confidence = 1.0
                        g.source_code_uri = new_source_uri
                        g.context7_id = new_c7_id
                        # Update sidebar if it's showing the same gem
                        if sidebar.display and sidebar.gem.name == gem.name:
                            sidebar.update_gem(g)
                        break
                
                self.update_table()
        
        self.push_screen(EditGemScreen(gem), handle_edit)

    def action_delete_gem(self):
        sidebar = self.query_one("#details-sidebar", GemDetails)
        if not sidebar.display or not hasattr(sidebar, 'gem'):
             # Try to get from table selection
            try:
                row_key, _ = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
                gem_name = row_key.value if hasattr(row_key, 'value') else str(row_key)
                gem = next((g for g in self.filtered_gems if g.name == gem_name), None)
                if not gem:
                    raise ValueError(f"Gem '{gem_name}' not found")
            except Exception:
                self.notify("Select a gem to delete", severity="warning")
                return
        else:
            gem = sidebar.gem

        def handle_delete(confirmed: bool):
            if confirmed:
                self.storage.delete_gem(gem.name)
                self.notify(f"Removed {gem.name} from database")
                if sidebar.display and sidebar.gem.name == gem.name:
                    sidebar.display = False
                self.action_refresh()
        
        self.push_screen(ConfirmDeleteScreen(gem.name), handle_delete)

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
                
                # Update local state
                for g in self.all_gems:
                    if g.name == gem_name:
                        g.context7_id = selected_id
                        sidebar = self.query_one("#details-sidebar", GemDetails)
                        if sidebar.display and sidebar.gem.name == gem_name:
                            sidebar.update_gem(g)
                        break
                
                self.update_table()

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
            # First, check if we already have a Context7 ID for this gem in our local state
            lib_id = None
            for g in self.all_gems:
                if g.name == target_name and g.context7_id:
                    lib_id = g.context7_id
                    self.call_from_thread(self.log_debug, f"Using stored Context7 ID for {target_name}: {lib_id}")
                    break
            
            # If not found in local state, try to verify via API
            if not lib_id:
                self.call_from_thread(self.log_debug, f"No stored ID for {target_name}, re-verifying...")
                lib_id = self.c7_service.verify_library(target_name)
            
            if not lib_id:
                self.call_from_thread(self.notify, f"No Context7 results found for {target_name}.", severity="warning")
                return

            self.call_from_thread(self.notify, f"Querying Context7 ({lib_id}) for {target_name}...")
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

    
    def get_txtai_agent(self):
        if not hasattr(self, '_txtai_agent') or self._txtai_agent is None:
            self._txtai_agent = TxtaiAgent()
        return self._txtai_agent


    def action_open_chat(self):
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = TabConstants.CHAT
        
        with self._selection_lock:
            if self._selected_gems:
                try:
                    chat_tab = self.query_one(AgentChatTab)
                    input_widget = chat_tab.query_one("#chat-input", Input)
                    
                    gem_list = ", ".join(sorted(list(self._selected_gems)))
                    context_prefix = f"Regarding the gems {gem_list}: "
                    
                    if input_widget.value and not input_widget.value.startswith("Regarding"):
                        input_widget.value = context_prefix + input_widget.value
                    elif not input_widget.value:
                        input_widget.value = context_prefix
                        
                    input_widget.focus()
                    
                    self._selected_gems.clear()
                    self._update_selection_status(0)
                    self.notify(f"Injected {len(gem_list.split(','))} gem(s) into chat context.")
                except Exception as e:
                    self.log_error(f"Failed to inject context into chat: {e}")


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
