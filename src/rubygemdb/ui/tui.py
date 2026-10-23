import os
import json
import threading
import argparse
from typing import Optional
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, DataTable, RadioSet, RadioButton, Label,
    Button, Markdown, ListView, ListItem, Input, TabbedContent, TabPane, RichLog,
)
from textual.screen import ModalScreen
from textual.timer import Timer
from textual import on, work

from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.models.categories import CATEGORIES
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
        pref = " | ★ preferred" if gem.preferred else ""
        self.query_one("#details-classification", Label).update(
            f"Category: {gem.classification.primary} (Conf: {gem.classification.confidence:.2f}){pref}\nSub-cats: {sub_cats}"
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
    """A screen to manually add a gem by name, with incremental (find-as-you-type)
    search over the local inventory plus debounced rubygems.org suggestions."""
    REMOTE_DEBOUNCE = 0.4

    def __init__(self, existing_names: Optional[list] = None):
        super().__init__()
        self.existing_names = sorted({n.lower(): n for n in (existing_names or [])}.values())
        self._local_matches: list[str] = []
        self._remote_matches: list[dict] = []
        self._query = ""
        self._remote_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="add-gem-dialog", classes="modal-dialog"):
            yield Label("Add New Gem", classes="section-title")
            yield Label(
                "Type to search the inventory and rubygems.org. "
                "Enter adds the typed name; Tab into the list to pick a match.",
                classes="help-text",
            )
            yield Input(placeholder="gem-name", id="gem-name-input")
            yield ListView(id="gem-suggestions")
            with Horizontal(id="dialog-buttons"):
                yield Button("Add Gem", id="add-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn", variant="error")

    def on_mount(self) -> None:
        self.query_one("#gem-name-input", Input).focus()
        self._render_suggestions()

    @on(Input.Changed, "#gem-name-input")
    def on_query_changed(self, event: Input.Changed) -> None:
        self._query = event.value.strip()
        self._update_local_matches()
        self._schedule_remote_search()

    @on(Input.Submitted, "#gem-name-input")
    def on_query_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value.strip())

    @on(ListView.Selected, "#gem-suggestions")
    def on_suggestion_selected(self, event: ListView.Selected) -> None:
        name = getattr(event.item, "gem_name", None)
        if name:
            self.dismiss(name)

    @on(Button.Pressed, "#add-btn")
    def on_add(self):
        name = self.query_one("#gem-name-input", Input).value.strip()
        if name:
            self.dismiss(name)

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel(self):
        self.dismiss(None)

    def _update_local_matches(self) -> None:
        """Instant substring filter over the local inventory (find-as-you-type)."""
        query = self._query.lower()
        if query:
            self._local_matches = [n for n in self.existing_names if query in n.lower()][:8]
        else:
            self._local_matches = []
        self._render_suggestions()

    def _schedule_remote_search(self) -> None:
        """Debounce rubygems.org lookups so each keystroke doesn't hit the API."""
        if self._remote_timer is not None:
            self._remote_timer.stop()
            self._remote_timer = None
        if len(self._query) < 2:
            self._remote_matches = []
            self._render_suggestions()
            return
        self._remote_timer = self.set_timer(self.REMOTE_DEBOUNCE, self._run_remote_search)

    def _run_remote_search(self) -> None:
        """Run the rubygems.org lookup off the UI thread (debounced by caller)."""
        self.run_worker(self._remote_search_work, thread=True, exclusive=True)

    def _remote_search_work(self) -> None:
        query = self._query
        service = getattr(self.app, "rg_service", None)
        if service is None:
            return
        results = service.search_gems(query)

        def apply(res: Optional[list]) -> None:
            if self._query == query:  # ignore stale in-flight responses
                self._remote_matches = res or []
                self._render_suggestions()

        self.app.call_from_thread(apply, results)

    def _render_suggestions(self) -> None:
        suggestions = self.query_one("#gem-suggestions", ListView)
        suggestions.clear()
        existing = {n.lower() for n in self.existing_names}

        for name in self._local_matches:
            item = ListItem(Label(f"{name} [dim]— already in database[/dim]"))
            setattr(item, "gem_name", name)
            suggestions.append(item)

        for gem in self._remote_matches:
            gem_name = gem.get("name")
            if not gem_name or gem_name.lower() in existing:
                continue
            desc = (gem.get("info") or "").strip()
            if len(desc) > 60:
                desc = desc[:60] + "..."
            item = ListItem(Label(f"{gem_name} [dim]— rubygems.org[/dim]\n[i]{desc}[/i]"))
            setattr(item, "gem_name", gem_name)
            suggestions.append(item)

        if self._query and len(suggestions) == 0:
            suggestions.append(ListItem(Label(f"No matches for '{self._query}'")))
        elif not self._query:
            suggestions.append(ListItem(Label("Start typing to search...")))

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
                for cat in CATEGORIES:
                    yield RadioButton(cat.label, id=f"edit_cls_{cat.slug}", value=(cat.slug == self.gem.classification.primary))
            
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
    """A chat tab for interacting with the Contextual GraphRAG agent."""

    def __init__(self, agent_loader_callback):
        super().__init__()
        self.agent_loader_callback = agent_loader_callback
        self.agent = None
        self._chat_messages: list[dict] = []
        self._query_queue: list[tuple[str, str]] = []
        self._thinking = False
        self.selected_gems: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="chat-split"):
            with Vertical(id="chat-main"):
                yield Label("RubyGemDB Contextual GraphRAG", id="chat-title")
                with Horizontal(id="selected-gems-bar"):
                    yield Label("Active Gems: None (select gems in Explorer to index)", id="selected-gems-label")
                yield Input(placeholder="Project intention (e.g. Asynchronous CLI pipeline)", id="intention-input")
                yield Button("Build GraphRAG Context", id="build-graphrag-btn", variant="primary")
                yield RichLog(id="chat-log", markup=True, wrap=True)
                from textual.widgets import Select
                yield Select([], prompt="Optional: Select a codebase memory project for context", id="project-input")
                yield Input(placeholder="Ask the GraphRAG agent a grounded question...", id="chat-input")
                with Horizontal(id="chat-actions"):
                    yield Button("Add Gem", id="chat-add-gem-btn", variant="success")
                    yield Button("Export to Markdown", id="export-chat-btn", variant="primary")
                    yield Button("Clear Chat", id="clear-chat-btn", variant="error")
            with Vertical(id="chat-trace"):
                yield Label("Container & GraphRAG Trace", id="trace-title", classes="section-title")
                yield RichLog(id="trace-log", markup=True, wrap=True)

    def update_selected_gems(self, gems: list[str]) -> None:
        """Update active gem selection from Explorer and refresh UI badges."""
        self.selected_gems = sorted(list({g.strip() for g in gems if g.strip()}))
        try:
            label = self.query_one("#selected-gems-label", Label)
            if self.selected_gems:
                badges = " | ".join(f"[bold cyan]{g}[/bold cyan]" for g in self.selected_gems)
                label.update(f"Active Gems ({len(self.selected_gems)}): {badges}")
            else:
                label.update("Active Gems: None (select gems in Explorer to index)")
        except Exception:
            pass

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
        self.log_widget.write(f"\n[bold {color}]{role.title()}:[/bold {color}]")
        
        if role == "agent":
            from rich.markdown import Markdown
            self.log_widget.write(Markdown(content))
        else:
            self.log_widget.write(content)

    def on_mount(self) -> None:
        self.log_widget = self.query_one("#chat-log", RichLog)
        self._add_message("agent", "System ready. Loading GraphRAG embeddings engine...")
        self.load_agent()
        self.load_projects()

    @work(thread=True)
    def load_projects(self):
        import subprocess
        import json
        import re
        from textual.widgets import Select
        try:
            result = subprocess.run(["/home/b08x/.local/bin/codebase-memory-mcp", "cli", "--json", "list_projects"], capture_output=True, text=True)
            if result.returncode == 0:
                output = result.stdout
                match = re.search(r'\{.*\}', output, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    text_content = data.get("content", [{}])[0].get("text", "{}")
                    projects = json.loads(text_content).get("projects", [])
                    options = [(f"{p['name']} ({p['root_path']})", p['name']) for p in projects]
                    
                    def update_ui():
                        select = self.query_one("#project-input", Select)
                        select.set_options(options)
                    
                    self.app.call_from_thread(update_ui)
        except Exception as e:
            self.app.call_from_thread(self.log_widget.write, f"[dim]Failed to load projects: {e}[/dim]")

    @work(thread=True)
    def load_agent(self):
        try:
            self.agent = self.agent_loader_callback()
            self.app.call_from_thread(self._add_message, "agent", "Ready! Select gems, define your project intention, and click 'Build GraphRAG Context'.")
        except Exception as e:
            self.app.call_from_thread(self._add_message, "error", f"Failed to load agent: {e}")

    @on(Button.Pressed, "#build-graphrag-btn")
    def on_build_graphrag_context(self) -> None:
        """Trigger containerized extraction and GraphRAG vector indexing in worker thread."""
        if not self.agent:
            self._add_message("error", "Agent still loading... please wait.")
            return

        if not self.selected_gems:
            self.notify("No gems selected. Select gems in the Explorer tab first.", severity="warning")
            return

        intention_widget = self.query_one("#intention-input", Input)
        intention = intention_widget.value.strip()

        self._start_thinking()
        self._add_trace(f"[bold cyan]Starting GraphRAG index build for {self.selected_gems}...[/bold cyan]")
        self.build_graphrag_worker(self.selected_gems, intention)

    @work(thread=True)
    def build_graphrag_worker(self, gems: list[str], intention: str) -> None:
        def progress_cb(msg: str) -> None:
            self.app.call_from_thread(self._add_trace, f"[cyan]➜[/cyan] {msg}")

        try:
            res = self.agent.set_graphrag_context(gems, intention=intention, progress_callback=progress_cb)
            summary_msg = (
                f"**GraphRAG Context Ready!**\n"
                f"- **Indexed Gems:** {', '.join(res.get('gems', []))}\n"
                f"- **Project Intention:** {res.get('intention') or 'None specified'}\n"
                f"- **AST & Doc Nodes:** {res.get('nodes', 0)}\n"
                f"- **Graph Edges:** {res.get('edges', 0)}\n\n"
                f"You can now ask grounded architectural questions about these gems."
            )
            self.app.call_from_thread(self._add_message, "agent", summary_msg)
            self.app.call_from_thread(self.notify, "GraphRAG Context ready!")
        except Exception as e:
            self.app.call_from_thread(self._add_message, "error", f"GraphRAG build failed: {e}")
            self.app.call_from_thread(self._add_trace, f"[red]Error: {e}[/red]")
        finally:
            self.app.call_from_thread(self._stop_thinking)

    @on(Input.Submitted, "#chat-input")
    def submit_query(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return

        event.input.value = ""
        try:
            from textual.widgets import Select
            val = self.query_one("#project-input", Select).value
            if val == Select.BLANK:
                project_context = ""
            else:
                project_context = str(val).strip()
        except Exception:
            project_context = ""
            
        self._add_message("user", query)
        
        if not self.agent:
            self._add_message("error", "Still loading... please wait.")
            return

        if getattr(self, "_thinking", False):
            self._query_queue.append((query, project_context))
            self._add_message("system", f"Added to queue. ({len(self._query_queue)} pending)")
            return

        self._thinking = True
        self.process_query(query, project_context)

    @work(thread=True)
    def process_query(self, query: str, project_context: Optional[str] = None) -> None:
        self.app.call_from_thread(self._start_thinking)
        self._thinking = True
        self.app.call_from_thread(self._add_trace, f"[cyan]➜[/cyan] Querying GraphRAG agent: {query[:60]}...")

        try:
            response = str(self.agent.chat(query, project_context=project_context))
            self._thinking = False
            self.app.call_from_thread(self._add_message, "agent", response)
        except Exception as e:
            self._thinking = False
            self.app.call_from_thread(self._add_message, "error", str(e))
            self.app.call_from_thread(self._add_trace, f"[red]Query error: {e}[/red]")
        finally:
            self.app.call_from_thread(self._stop_thinking)
            if hasattr(self, "_query_queue") and self._query_queue:
                next_item = self._query_queue.pop(0)
                self._thinking = True
                self.app.call_from_thread(self.process_query, next_item[0], next_item[1])
            else:
                self._thinking = False

    def _start_thinking(self):
        self.log_widget.loading = True
        self._add_trace("[b yellow]Starting task execution...[/b yellow]")
        
    def _add_trace(self, text: str):
        try:
            self.query_one("#trace-log", RichLog).write(text)
        except Exception:
            pass
            
    def _stop_thinking(self):
        self.log_widget.loading = False
        self._add_trace("[b green]Execution completed.[/b green]")

    @on(Button.Pressed, "#chat-add-gem-btn")
    def on_chat_add_gem(self) -> None:
        """Open the incremental-search dialog to add a gem from agent chat."""
        app = self.app
        if not isinstance(app, GemApp):
            return
        existing = [g.name for g in app.all_gems]

        def handle_add(name: Optional[str]) -> None:
            if not name:
                return
            self._add_message("system", f"Adding '{name}' to the gem database...")
            try:
                app.add_gem_worker(name)
            except Exception as e:
                self._add_message("error", f"Failed to add gem '{name}': {e}")

        self.app.push_screen(AddGemScreen(existing), handle_add)

    @on(Button.Pressed, "#export-chat-btn")
    def on_export_chat(self) -> None:
        """Export the active GraphRAG conversation as markdown and JSON."""
        if not self.agent or not hasattr(self.agent, "export_transcript"):
            self.notify("Agent not initialized.", severity="warning")
            return
        try:
            md_path, json_path = self.agent.export_transcript()
            self.notify(f"Chat exported to {md_path}")
            self.log_widget.write(f"\n[dim]Conversation exported to {md_path} and {json_path}[/dim]")
        except Exception as e:
            self.notify(f"Export failed: {e}", severity="error")

    @on(Button.Pressed, "#clear-chat-btn")
    def on_clear_chat(self) -> None:
        self._chat_messages.clear()
        self.log_widget.clear()
        if self.agent is not None and hasattr(self.agent, "clear_chat"):
            try:
                self.agent.clear_chat()
            except Exception:
                pass
        self._add_message("agent", "Chat cleared. Ready for queries.")


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
        ("p", "toggle_preferred", "Prefer"),
        ("plus", "add_gem", "Add"),
        ("slash", "focus_search", "Search"),
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
    #gem-search-input {
        height: 3;
        margin-bottom: 1;
        border: solid $background;
    }
    #gem-suggestions {
        height: auto;
        max-height: 12;
        margin: 1 0;
        border: solid $background;
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
    #chat-split {
        height: 100%;
        width: 100%;
    }
    #chat-main {
        width: 70%;
        padding: 1 2;
        height: 100%;
    }
    #chat-trace {
        width: 30%;
        padding: 1 2;
        height: 100%;
        border-left: vkey $background;
    }
    #trace-log {
        height: 1fr;
        border: solid $background;
        background: $surface;
    }
    #selected-gems-bar {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        background: $surface;
        border: solid $accent;
    }
    #selected-gems-label {
        width: 100%;
        color: $text;
    }
    #intention-input {
        margin-bottom: 1;
    }
    #build-graphrag-btn {
        width: 100%;
        margin-bottom: 1;
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
        width: 1fr;
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
        self.search_filter: str = ""
        self._selected_gems = set()
        self._selection_lock = threading.Lock()
        self._bulk_update_queue = []
        self._bulk_update_aborted = False
        self._inventory_loaded = False
        self.table: DataTable = None  # Type annotation for mypy
        
        self.classifications = [(c.slug, c.label) for c in CATEGORIES]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Filter Classification", classes="section-title")
                with RadioSet(id="class_filter"):
                    yield RadioButton("All Categories", id="cls_any", value=True)
                    for cls_slug, cls_label in self.classifications:
                        yield RadioButton(cls_label, id=f"cls_{cls_slug}")
                
                yield Label("System Stats", classes="section-title")
                yield Label("Total Gems: 0", id="gem-count-label")
                yield Label("Last Update: Never", id="last-update-label")

            with Vertical(id="main-area"):
                with TabbedContent(id="main-tabs"):
                    with TabPane("Explorer", id=TabConstants.EXPLORER):
                        yield Input(
                            placeholder="Type to filter gems (name, category, description)...",
                            id="gem-search-input",
                        )
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

            # Incremental text filter (find-as-you-type)
            search = self.search_filter
            if search:
                filtered = [
                    g for g in filtered
                    if search in g.name.lower()
                    or search in (g.classification.primary or "").lower()
                    or search in (g.description or "").lower()
                ]

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
                display_name = f"★ {gem.name}" if gem.preferred else gem.name
                self.table.add_row(
                    display_name,
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

    @on(Input.Changed, "#gem-search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Incremental search: refilter the gem table on every keystroke."""
        self.search_filter = event.value.strip().lower()
        self.update_table()

    def action_focus_search(self) -> None:
        """Focus the find-as-you-type filter (bound to /)."""
        self.query_one("#gem-search-input", Input).focus()

    @on(TabbedContent.TabActivated)
    def on_tab_activated(self, event: TabbedContent.TabActivated):
        """Sync selected gems and manage sidebar visibility."""
        sidebar = self.query_one("#sidebar")
        if event.pane.id == TabConstants.EXPLORER:
            sidebar.display = True
        else:
            sidebar.display = False
            
        if event.pane.id == TabConstants.EXPORT:
            export_tab = self.query_one("#export-tab", ExportTab)
            with self._selection_lock:
                selected = list(self._selected_gems)
            export_tab.set_gems(selected)
            self.log_debug(f"Export tab activated, synced {len(selected)} gems")

        if event.pane.id == TabConstants.CHAT:
            try:
                chat_tab = self.query_one(AgentChatTab)
                with self._selection_lock:
                    selected = list(self._selected_gems)
                chat_tab.update_selected_gems(selected)
                self.log_debug(f"Chat tab activated, synced {len(selected)} gems")
            except Exception as e:
                self.log_error(f"Failed to sync selected gems to chat tab: {e}")

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

        self.push_screen(AddGemScreen([g.name for g in self.all_gems]), handle_add)

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

    def _current_gem(self) -> Optional[GemEntry]:
        """The gem the user is acting on: details sidebar, else table cursor."""
        sidebar = self.query_one("#details-sidebar", GemDetails)
        if sidebar.display and hasattr(sidebar, "gem"):
            return sidebar.gem
        try:
            row_key, _ = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
            gem_name = row_key.value if hasattr(row_key, "value") else str(row_key)
            return next((g for g in self.filtered_gems if g.name == gem_name), None)
        except Exception:
            return None

    def action_toggle_preferred(self) -> None:
        """Toggle the preferred flag on the current gem (bound to 'p')."""
        gem = self._current_gem()
        if gem is None:
            self.notify("Select a gem first", severity="warning")
            return

        gem.preferred = not gem.preferred
        try:
            self.storage.set_preferred([gem.name], gem.preferred)
        except Exception as e:
            gem.preferred = not gem.preferred
            self.handle_error(f"Failed to toggle preferred for {gem.name}: {e}", e)
            return

        state = "preferred" if gem.preferred else "no longer preferred"
        self.notify(f"{gem.name} {state}")
        self.update_table()

        sidebar = self.query_one("#details-sidebar", GemDetails)
        if sidebar.display and getattr(sidebar, "gem", None) and sidebar.gem.name == gem.name:
            sidebar.update_gem(gem)

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
            selected = list(self._selected_gems)
        try:
            chat_tab = self.query_one(AgentChatTab)
            chat_tab.update_selected_gems(selected)
            input_widget = chat_tab.query_one("#chat-input", Input)
            input_widget.focus()
            if selected:
                self.notify(f"Synced {len(selected)} gem(s) into GraphRAG context.")
        except Exception as e:
            self.log_error(f"Failed to focus chat: {e}")


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
