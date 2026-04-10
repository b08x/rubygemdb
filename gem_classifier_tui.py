#!/usr/bin/env python3
import os
import sys
import csv
import json
import time
import requests
import subprocess
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Header, Footer, DataTable, RadioSet, RadioButton, Label, Button, Markdown, ListView, ListItem, ProgressBar, Digits

# Import the classification logic from gem_classifier.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import gem_classifier as gc
    # Ensure caches use absolute paths based on the gem_classifier.py file location
    gc_dir = os.path.dirname(gc.__file__)
    gc.gem_cache = gc.load_cache(os.path.join(gc_dir, gc.CACHE_FILE))
    gc.llm_cache = gc.load_cache(os.path.join(gc_dir, gc.LLM_CACHE_FILE))
except ImportError:
    print("Error: Could not import gem_classifier.py. Make sure it is in the same directory.")
    sys.exit(1)

import random

# -----------------------------
# Robust API Helper
# -----------------------------

def safe_request(method, url, retries=5, backoff_factor=1.5, jitter=0.2, **kwargs):
    """
    Performs a network request with exponential backoff, jitter, and retry logic.
    """
    for attempt in range(retries):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code == 429:  # Rate limited
                wait_time = backoff_factor ** attempt + (random.random() * jitter)
                time.sleep(wait_time)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            if attempt == retries - 1:
                raise e
            wait_time = backoff_factor ** attempt + (random.random() * jitter)
            time.sleep(wait_time)
    return None

def fetch_gem_info_robust(name: str) -> tuple[dict, bool]:
    """Wrapper for RubyGems API with robust retry and caching. Returns (data, is_new)."""
    if name in gc.gem_cache:
        return gc.gem_cache[name], False

    url = gc.RUBYGEMS_API_URL.format(name=name)
    try:
        data = safe_request("GET", url, timeout=5)
        if data:
            gc.gem_cache[name] = data
            # Fix: Ensure gem_cache is saved to its proper file path
            gc.save_cache(os.path.join(os.path.dirname(gc.__file__), gc.CACHE_FILE), gc.gem_cache)
            return data, True
    except Exception:
        pass
    return None, False

def call_llm_robust(prompt: str) -> tuple[dict, bool]:
    """Wrapper for Devstral LLM API with robust retry and caching. Returns (result, is_new)."""
    key = gc.prompt_hash(prompt)
    if key in gc.llm_cache:
        return gc.llm_cache[key], False

    headers = {"Authorization": f"Bearer {gc.LLM_API_KEY}"}
    payload = {
        "model": gc.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0
    }

    try:
        data = safe_request("POST", gc.LLM_ENDPOINT, json=payload, headers=headers, timeout=15)
        if data:
            result = json.loads(data["choices"][0]["message"]["content"])
            gc.llm_cache[key] = result
            # Fix: Ensure llm_cache is saved to its proper file path
            gc.save_cache(os.path.join(os.path.dirname(gc.__file__), gc.LLM_CACHE_FILE), gc.llm_cache)
            return result, True
    except Exception:
        pass
    return None, False

CLASSIFIED_GEMS_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "classified_gems.json")

def load_results_cache():
    if os.path.exists(CLASSIFIED_GEMS_CACHE):
        try:
            with open(CLASSIFIED_GEMS_CACHE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_results_cache(data):
    try:
        with open(CLASSIFIED_GEMS_CACHE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

class GemDetails(Vertical):
    """A widget to display detailed gem information."""
    
    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="details-container"):
            yield Label("", id="details-name", classes="details-title")
            yield Label("", id="details-version")
            yield Markdown("", id="details-description")
            
            yield Label("Risk Scores", classes="section-title")
            yield Label("", id="details-risks")
            
            yield Label("Runtime Dependencies", classes="section-title")
            yield Label("Select a dependency to fetch its cheatsheet instead.", classes="help-text")
            yield ListView(id="details-deps-list")
            
            yield Button("Fetch Context7 Cheatsheet", id="fetch-cheatsheet-btn", variant="primary")
            yield Button("Close", id="close-details-btn", variant="error")

    def update_gem(self, gem_data: dict):
        self.gem_data = gem_data
        self.target_name = gem_data.get("name", "Unknown")
        
        self.query_one("#details-name", Label).update(self.target_name)
        version = gem_data.get("info", {}).get("version", "N/A") if gem_data.get("info") else "N/A"
        self.query_one("#details-version", Label).update(f"Version: {version}")
        
        desc = gem_data.get("info", {}).get("info", "No description available.") if gem_data.get("info") else "No description available."
        self.query_one("#details-description", Markdown).update(desc)
        
        risks = gem_data.get("risks", {})
        risk_text = f"Invasiveness: {risks.get('invasiveness', '?')}\nCoupling: {risks.get('coupling', '?')}\nLeak: {risks.get('abstraction_leak', '?')}"
        self.query_one("#details-risks", Label).update(risk_text)
        
        # Populate Dependencies
        deps_list = self.query_one("#details-deps-list", ListView)
        deps_list.clear()
        
        deps = gem_data.get("deps", [])
        if not deps:
            deps_list.append(ListItem(Label("No runtime dependencies")))
        else:
            for dep in deps:
                deps_list.append(ListItem(Label(dep), id=f"dep-{dep}"))
        
        # Reset button label to main gem
        self.update_button_label(self.target_name)

    def update_button_label(self, name: str):
        self.target_name = name
        self.query_one("#fetch-cheatsheet-btn", Button).label = f"Fetch Context7 Cheatsheet: {name}"


class GemExplorerApp(App):
    """A reactive TUI application for exploring and classifying Ruby Gems."""

    CSS = """
    Horizontal {
        height: 1fr;
    }
    #sidebar {
        width: 35;
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
    .help-text {
        color: $text-muted;
        text-style: italic;
        margin-bottom: 1;
    }
    #main-area {
        width: 1fr;
        layout: vertical;
    }
    #progress-area {
        height: auto;
        padding: 1 2;
        background: $surface;
        border-bottom: vkey $background;
        display: block;
    }
    #progress-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 0;
    }
    #stats-row {
        height: 1;
        margin-top: 1;
    }
    .stat-label {
        text-style: bold;
        margin-right: 1;
    }
    #current-gem-label {
        width: 1fr;
        color: $warning;
    }
    #processed-count {
        width: auto;
        color: $success;
        margin-left: 1;
    }
    DataTable {
        height: 1fr;
    }
    
    #details-sidebar {
        width: 45;
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
    #details-version {
        color: $text-muted;
        margin-bottom: 1;
    }
    #details-description {
        margin-bottom: 1;
        height: auto;
    }
    #details-deps-list {
        height: auto;
        max-height: 15;
        margin-bottom: 1;
        border: solid $background;
    }
    #fetch-cheatsheet-btn {
        margin-top: 1;
        width: 100%;
    }
    #close-details-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, csv_file: str):
        super().__init__()
        self.csv_file = csv_file
        self.all_gems = []
        self.filtered_gems = []
        self.columns = ["Name", "Version", "Primary Classification", "Invasiveness", "Coupling"]
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
                with Vertical(id="progress-area"):
                    yield Label("Processing Gems...", id="progress-title")
                    yield ProgressBar(id="processing-bar", show_eta=True)
                    with Horizontal(id="stats-row"):
                        yield Label("Current:", classes="stat-label")
                        yield Label("Initializing...", id="current-gem-label")
                        yield Label("Processed:", classes="stat-label")
                        yield Digits("0", id="processed-count")
                yield DataTable(id="gems_table")
            
            yield GemDetails(id="details-sidebar")
            
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns(*self.columns)
        self.process_csv()

    @work(thread=True)
    def process_csv(self) -> None:
        if not os.path.exists(self.csv_file):
            self.call_from_thread(self.notify, f"CSV file not found: {self.csv_file}", severity="error")
            return

        with open(self.csv_file, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        total = len(rows)
        self.call_from_thread(self.notify, f"Processing {total} gems...")
        
        # Load local classification results cache
        results_cache = load_results_cache()
        
        # Initialize progress bar
        pbar = self.query_one("#processing-bar", ProgressBar)
        self.call_from_thread(setattr, pbar, "total", total)
        
        processed = 0
        for row in rows:
            name = row.get("name") or row.get("gem")
            group = row.get("group")
            if not name:
                processed += 1
                self.call_from_thread(pbar.advance)
                continue
            
            # Check results cache first
            if name in results_cache:
                self.all_gems.append(results_cache[name])
                self.call_from_thread(self.update_table)
                processed += 1
                self.call_from_thread(pbar.advance)
                self.call_from_thread(self.query_one("#processed-count", Digits).update, str(processed))
                continue

            self.call_from_thread(self.query_one("#current-gem-label", Label).update, f"Fetching {name}...")
            
            info, is_new_info = fetch_gem_info_robust(name)
            if is_new_info:
                time.sleep(gc.RATE_LIMIT_DELAY)
            
            classification, signals, conf, deps = gc.classify_gem(name, info, group)
            
            if conf < 0.7:
                self.call_from_thread(self.query_one("#current-gem-label", Label).update, f"LLM Classifying {name}...")
                prompt = gc.build_prompt(name, info, deps)
                llm, is_new_llm = call_llm_robust(prompt)
                if is_new_llm:
                    time.sleep(gc.LLM_RATE_DELAY)
                if llm and llm.get("confidence", 0) > 0.6:
                    classification["primary"] = llm["primary"]
            
            risks = gc.score_gem(classification, deps)
            
            gem_record = {
                "name": name,
                "info": info,
                "classification": classification,
                "signals": signals,
                "deps": deps,
                "risks": risks
            }
            
            self.all_gems.append(gem_record)
            results_cache[name] = gem_record
            save_results_cache(results_cache)
            
            self.call_from_thread(self.update_table)
            
            processed += 1
            self.call_from_thread(pbar.advance)
            self.call_from_thread(self.query_one("#processed-count", Digits).update, str(processed))

        self.call_from_thread(self.notify, "Finished processing gems.")
        self.call_from_thread(self.query_one("#progress-title", Label).update, "[b]Processing Complete[/b]")
        self.call_from_thread(self.query_one("#current-gem-label", Label).update, "All gems classified.")


    def get_gem_row(self, gem: dict):
        info = gem.get("info", {})
        version = info.get("version", "?") if info else "?"
        cls = gem.get("classification", {}).get("primary", "unknown")
        risks = gem.get("risks", {})
        
        return [
            gem.get("name", "?"),
            version,
            cls,
            str(risks.get("invasiveness", "?")),
            str(risks.get("coupling", "?"))
        ]

    @on(RadioSet.Changed)
    def update_table(self, event=None) -> None:
        cls_set = self.query_one("#class_filter", RadioSet)
        active_id = cls_set.pressed_button.id if cls_set.pressed_button else "cls_any"

        filtered = []
        for g in self.all_gems:
            if active_id != "cls_any":
                target_cls = active_id.replace("cls_", "")
                if g.get("classification", {}).get("primary") != target_cls:
                    continue
            filtered.append(g)

        self.filtered_gems = filtered
        
        table = self.query_one(DataTable)
        table.clear()
        for index, g in enumerate(filtered):
            row = self.get_gem_row(g)
            table.add_row(*row, key=str(index))

    @on(DataTable.RowSelected)
    def show_details(self, event: DataTable.RowSelected) -> None:
        try:
            index = int(event.row_key.value)
            gem_data = self.filtered_gems[index]
            sidebar = self.query_one("#details-sidebar", GemDetails)
            sidebar.update_gem(gem_data)
            sidebar.display = True
        except Exception as e:
            self.notify(f"Error loading details: {e}", severity="error")

    @on(ListView.Selected, "#details-deps-list")
    def on_dependency_selected(self, event: ListView.Selected):
        item = event.item
        if not item.id or not item.id.startswith("dep-"):
            return
        dep_name = item.id.replace("dep-", "")
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

    @work(thread=True)
    def fetch_cheatsheet(self, target_name: str) -> None:
        api_key = os.getenv("CONTEXT7_API_KEY")
        if not api_key:
            self.call_from_thread(self.notify, "Error: CONTEXT7_API_KEY environment variable not set", severity="error")
            return

        out_dir = "/home/b08x/Workspace/Tools/cheatsheets"
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"{target_name}_cheatsheet.md")

        self.call_from_thread(self.notify, f"Fetching Context7 info for {target_name}...")
        
        search_url = f"https://context7.com/api/v2/libs/search?libraryName={target_name}&query=ruby+gem+{target_name}+documentation"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        try:
            # 1. Resolve Library ID
            search_data = safe_request("GET", search_url, headers=headers, timeout=10)
            results = search_data.get("results", [])
            
            if not results:
                self.call_from_thread(self.notify, f"No Context7 results found for {target_name}.", severity="warning")
                return

            lib_id = results[0]["id"]
            
            # 2. Query for specific use cases
            self.call_from_thread(self.notify, f"Querying use cases for {target_name}...")
            context_query = (
                "Provide a few examples of how this gem might be used in the context of "
                "a genai application, nlp text processing, or as a systems tool."
            )
            # Encode query for URL
            import urllib.parse
            encoded_query = urllib.parse.quote(context_query)
            context_url = f"https://context7.com/api/v2/context?libraryId={lib_id}&query={encoded_query}"
            
            # Note: Context7 context endpoint returns raw text, not JSON
            context_response = requests.get(context_url, headers=headers, timeout=30)
            context_response.raise_for_status()
            context_text = context_response.text

            # 3. Format and save the cheatsheet
            block = f"# {target_name} Cheatsheet\n\n"
            block += f"**Library ID**: `{lib_id}`\n\n"
            block += "## Targeted Usage Examples (GenAI / NLP / Systems)\n\n"
            block += context_text
            block += "\n\n---\n"
            block += "### Additional Library IDs\n"
            for res in results[:3]:
                block += f"- `{res['id']}` ({res['title']})\n"
            
            block += f"\n*Generated by Gem Classifier TUI for {target_name}*\n"
            
            with open(out_file, "w") as f:
                f.write(block)
                
            self.call_from_thread(self.notify, f"Saved cheatsheet to {out_file}")
            
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to fetch cheatsheet: {str(e)}", severity="error")



if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ./gem_classifier_tui.py <path_to_gems.csv>")
        sys.exit(1)
        
    csv_path = sys.argv[1]
    app = GemExplorerApp(csv_file=csv_path)
    app.run()
