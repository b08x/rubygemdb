import os
import sys
import json
import requests
import argparse
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Header, Footer, DataTable, RadioSet, RadioButton, Label, Button, Markdown, ListView, ListItem

from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.services.context7 import Context7Service
from rubygemdb.storage.sqlite_storage import SQLiteStorage
from rubygemdb.core.config import settings

class GemApp(App):
    TITLE = "RubyGemDB Explorer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, inventory_path=None):
        super().__init__()
        self.inventory_path = inventory_path
        self.rg_service = RubyGemsService()
        self.llm_service = LLMService()
        self.c7_service = Context7Service()
        self.classifier = GemClassifier(self.rg_service, self.llm_service)
        self.storage = SQLiteStorage(rubygems_service=self.rg_service, context7_service=self.c7_service)
        
        # Populate inventory if CSV provided
        if self.inventory_path:
            self.storage.load_inventory(self.inventory_path)
            
        self.gems = self.storage.load_classified_gems()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Gems")
                self.table = DataTable()
                yield self.table
            with Vertical(id="details"):
                yield Label("Details", id="details-label")
                self.details_markdown = Markdown()
                yield ScrollableContainer(self.details_markdown)
        yield Footer()

    def on_mount(self) -> None:
        self.table.add_columns("Name", "Category", "Confidence")
        self.update_table()

    def update_table(self):
        self.table.clear()
        for gem in self.gems:
            self.table.add_row(
                gem.name, 
                gem.classification.primary, 
                f"{gem.classification.confidence:.2f}"
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        gem_name = self.table.get_row_at(event.cursor_row)[0]
        gem = next((g for g in self.gems if g.name == gem_name), None)
        if gem:
            self.show_details(gem)

    def show_details(self, gem):
        md = f"""# {gem.name}
**Category**: {gem.classification.primary}
**Confidence**: {gem.classification.confidence:.2f}

## Description
{gem.description or "No description available."}

## Risks
- Invasiveness: {gem.risks.invasiveness}/5
- Coupling: {gem.risks.coupling}/4
- Abstraction Leak: {gem.risks.abstraction_leak}

## Dependencies
{", ".join(gem.dependencies) if gem.dependencies else "None"}
"""
        self.details_markdown.update(md)

def run_tui():
    parser = argparse.ArgumentParser(description="RubyGemDB Explorer TUI")
    parser.add_argument("csv", nargs="?", help="Optional path to gems inventory CSV to populate/update DB")
    args = parser.parse_args()
    
    app = GemApp(inventory_path=args.csv)
    app.run()

if __name__ == "__main__":
    run_tui()
