import argparse
import yaml
import os
import logging
import requests
from collections import defaultdict
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table
from rich.prompt import Confirm, Prompt

from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.services.context7 import Context7Service
from rubygemdb.storage.sqlite_storage import SQLiteStorage

console = Console()
logging.basicConfig(level=logging.INFO, format="%(message)s")

import re

def verify_url(url: str) -> bool:
    if not url:
        return False
    try:
        r = requests.head(url, allow_redirects=True, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code == 200
    except Exception:
        return False

def check_repo_match(gem_name: str, url: str) -> bool:
    """Returns True if the URL loosely matches the gem name, False otherwise."""
    if not url:
        return True
    
    # Extract the last part of the URL (repo name)
    # e.g., https://github.com/rails/rails -> rails
    repo_name = url.rstrip("/").split("/")[-1].lower()
    gem_name = gem_name.lower()
    
    # Normalize by removing common separators
    norm_repo = re.sub(r'[^a-z0-9]', '', repo_name)
    norm_gem = re.sub(r'[^a-z0-9]', '', gem_name)
    
    # Check if they loosely match (one contains the other)
    if not norm_gem or not norm_repo:
        return True
    
    return norm_gem in norm_repo or norm_repo in norm_gem

def run_process(args):
    os.makedirs(args.out, exist_ok=True)
    
    rg_service = RubyGemsService()
    llm_service = LLMService()
    c7_service = Context7Service()
    classifier = GemClassifier(rg_service, llm_service)
    storage = SQLiteStorage(rubygems_service=rg_service, context7_service=c7_service)

    # 1. Load CSV if provided, else use existing DB inventory
    if args.csv:
        console.print(f"[cyan]Loading inventory from CSV: {args.csv}[/cyan]")
        inventory_items = storage.load_inventory(args.csv)
        # load_inventory returns GemInventoryItem objects
        gems = [{"name": item.name, "homepage": item.homepage, "source_code_uri": item.source_code_uri, "description": item.description, "context7_id": item.context7_id} for item in inventory_items]
    else:
        console.print("[cyan]No CSV provided, using existing database inventory...[/cyan]")
        gems = storage.get_all_inventory_gems()
        
    console.print(f"\n[bold magenta]--- PHASE 1: Metadata Verification ({len(gems)} gems) ---[/bold magenta]")
    
    deleted_count = 0
    updated_count = 0
    valid_gems = []

    if getattr(args, "skip_verify", False):
        console.print("[yellow]Skipping Phase 1 verification as requested...[/yellow]")
        valid_gems = gems
    else:
        for gem in gems:
            name = gem["name"]
            console.print(f"Verifying {name}...")
            info = rg_service.fetch_gem_info(name)
            if not info:
                if Confirm.ask(f"  [red]Gem '{name}' not found on RubyGems (hallucinated?). Delete it?[/red]"):
                    storage.delete_gem(name)
                    deleted_count += 1
                    console.print(f"  [red]Deleted {name}.[/red]")
                else:
                    console.print(f"  [yellow]Skipped deletion of {name}.[/yellow]")
                    valid_gems.append(gem)
            else:
                old_homepage = gem.get("homepage") or ""
                old_source = gem.get("source_code_uri") or ""
                old_desc = gem.get("description") or ""
                old_c7 = gem.get("context7_id") or ""
                
                new_homepage = info.get("homepage_uri") or ""
                new_source = info.get("source_code_uri") or ""
                new_desc = info.get("info") or ""
                
                # Fallback: if source_uri is missing from API but homepage is github/gitlab, use it
                if not new_source and new_homepage and ("github.com" in new_homepage or "gitlab.com" in new_homepage):
                    new_source = new_homepage
                    
                final_homepage = new_homepage if new_homepage else old_homepage
                if not new_homepage and old_homepage:
                    console.print(f"  [yellow]Kept existing manual homepage:[/yellow] {final_homepage}")
                    
                final_source = new_source if new_source else old_source
                if not new_source and old_source:
                    console.print(f"  [yellow]Kept existing manual source URI:[/yellow] {final_source}")
                    
                # VERIFY URL VALIDITY & MATCH
                if final_source:
                    valid = verify_url(final_source)
                    matched = check_repo_match(name, final_source)
                    
                    if not valid:
                        console.print(f"  [red]Warning: source_code_uri '{final_source}' returned 404 or failed to resolve.[/red]")
                    elif not matched:
                        console.print(f"  [magenta]Warning: Repository name in '{final_source}' doesn't match gem name '{name}'.[/magenta]")
                        
                    if not valid or not matched:
                        choice = Prompt.ask("  Enter a valid URL to replace it, or press Enter to keep it anyway", default="")
                        if choice.strip():
                            final_source = choice.strip()
                
                final_desc = new_desc if new_desc else old_desc
                
                # Context7 fallback
                final_c7 = old_c7
                if not final_c7:
                    search_query = name
                    while True:
                        c7_results = c7_service.search_libraries(search_query)
                        if c7_results:
                            fetched_c7 = c7_results[0].get("id") or c7_results[0].get("libraryId")
                            console.print(f"  [cyan]Found new context7_id:[/cyan] {fetched_c7} (Name: {c7_results[0].get('name', 'N/A')})")
                            if Confirm.ask(f"  Accept context7_id '{fetched_c7}' for gem '{name}'?", default=False):
                                final_c7 = fetched_c7
                                break
                            else:
                                console.print("  [cyan]Other matches found:[/cyan]")
                                for i, res in enumerate(c7_results, start=1):
                                    lib_id = res.get("id") or res.get("libraryId")
                                    console.print(f"    {i}. {lib_id} (Name: {res.get('name', 'N/A')})")
                                    
                                choice = Prompt.ask("  Select a number, type a new search query, or press Enter to skip", default="")
                                if choice.isdigit() and 1 <= int(choice) <= len(c7_results):
                                    final_c7 = c7_results[int(choice)-1].get("id") or c7_results[int(choice)-1].get("libraryId")
                                    console.print(f"  [green]Selected {final_c7}[/green]")
                                    break
                                elif choice.strip():
                                    # User typed a new search query
                                    search_query = choice.strip()
                                    console.print(f"  [cyan]Searching Context7 for '{search_query}'...[/cyan]")
                                    continue
                                else:
                                    console.print("  [yellow]Leaving context7_id blank.[/yellow]")
                                    final_c7 = ""
                                    break
                        else:
                            console.print(f"  [yellow]No matches found for '{search_query}'.[/yellow]")
                            choice = Prompt.ask("  Type a new search query to try again, or press Enter to skip", default="")
                            if choice.strip():
                                search_query = choice.strip()
                                console.print(f"  [cyan]Searching Context7 for '{search_query}'...[/cyan]")
                                continue
                            else:
                                console.print("  [yellow]Leaving context7_id blank.[/yellow]")
                                console.print("  [magenta]Note: Consider adding this gem to Context7 DB.[/magenta]")
                                final_c7 = ""
                                break
                else:
                    pass # Silent when keeping existing to reduce noise
    
                
                storage.update_gem_verification(
                    name=name,
                    homepage=final_homepage,
                    source_code_uri=final_source,
                    description=final_desc,
                    context7_id=final_c7
                )
                updated_count += 1
                valid_gems.append({
                    "name": name,
                    "homepage": final_homepage,
                    "source_code_uri": final_source,
                    "description": final_desc,
                    "context7_id": final_c7
                })

        console.print(f"[green]Phase 1 Complete! Updated metadata for {updated_count} gems. Deleted {deleted_count}.[/green]\n")
    
    console.print(f"[bold magenta]--- PHASE 2: Heuristic & LLM Classification ---[/bold magenta]")
    
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
        task = progress.add_task("[cyan]Classifying gems...", total=len(valid_gems))

        for gem in valid_gems:
            name = gem["name"]
            progress.update(task, description=f"[cyan]Analyzing [bold]{name}[/bold]...")
            
            # Fetch category from DB if available, else None
            # Here we just pass the metadata we just verified
            gem_entry = classifier.classify(
                name, 
                category=None, # Category hint was from CSV, but we rely on heuristics/LLM
                homepage=gem["homepage"],
                source_code_uri=gem["source_code_uri"],
                context7_id=gem["context7_id"]
            )
            results.append(gem_entry)
            categorized[gem_entry.classification.primary].append(gem_entry.model_dump())
            progress.advance(task)

    # Save to unified cache for TUI
    storage.save_classified_gems(results)

    # Save YAMLs per category
    for cat, items in categorized.items():
        with open(f"{args.out}/{cat}.yaml", "w") as f:
            yaml.dump(items, f, sort_keys=False)

    table = Table(title="Classification Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="magenta", justify="right")

    for cat in sorted(categorized.keys()):
        table.add_row(cat, str(len(categorized[cat])))

    console.print(table)
    console.print(f"[green]All processes complete! Saved reports to {args.out}/[/green]")

def run_cli():
    parser = argparse.ArgumentParser(description="Ruby Gem Classifier")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Command to run")
    
    # Unified Process Subcommand
    process_parser = subparsers.add_parser("process", help="Verify metadata and classify gems (Combined Pipeline)")
    process_parser.add_argument("csv", nargs="?", help="Optional path to initial gems inventory CSV")
    process_parser.add_argument("--out", default="output", help="Output directory for YAML files")
    process_parser.add_argument("--skip-verify", action="store_true", help="Skip Phase 1 metadata verification and go straight to Phase 2")
    
    args = parser.parse_args()

    if args.command == "process":
        run_process(args)

if __name__ == "__main__":
    run_cli()
