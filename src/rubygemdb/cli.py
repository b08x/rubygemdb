import argparse
import yaml
import os
import logging
from collections import defaultdict
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table

from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.services.context7 import Context7Service
from rubygemdb.storage.sqlite_storage import SQLiteStorage

console = Console()
logging.basicConfig(level=logging.INFO, format="%(message)s")

def run_cli():
    parser = argparse.ArgumentParser(description="Ruby Gem Classifier")
    parser.add_argument("csv", help="Path to gems inventory CSV")
    parser.add_argument("--out", default="output", help="Output directory for YAML files")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    
    rg_service = RubyGemsService()
    llm_service = LLMService()
    c7_service = Context7Service()
    classifier = GemClassifier(rg_service, llm_service)
    storage = SQLiteStorage(rubygems_service=rg_service, context7_service=c7_service)

    inventory = storage.load_inventory(args.csv)
    results = []
    categorized = defaultdict(list)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Classifying gems...", total=len(inventory))

        for item in inventory:
            progress.update(task, description=f"[cyan]Analyzing [bold]{item.name}[/bold]...")
            gem_entry = classifier.classify(
                item.name, 
                item.category,
                homepage=item.homepage,
                source_code_uri=item.source_code_uri,
                context7_id=item.context7_id
            )
            results.append(gem_entry)
            categorized[gem_entry.classification.primary].append(gem_entry.model_dump())
            progress.advance(task)

    # Save to unified cache for TUI
    storage.save_classified_gems(results)

    # Save YAMLs per category
    for cat, gems in categorized.items():
        with open(f"{args.out}/{cat}.yaml", "w") as f:
            yaml.dump(gems, f, sort_keys=False)

    table = Table(title="Classification Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="magenta", justify="right")

    for cat in sorted(categorized.keys()):
        table.add_row(cat, str(len(categorized[cat])))

    console.print(table)

if __name__ == "__main__":
    run_cli()
