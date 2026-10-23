import argparse
import yaml  # type: ignore
import os
import shutil
import logging
import requests
from collections import defaultdict
from datetime import datetime
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table
from rich.prompt import Confirm, Prompt

from rubygemdb.core.config import settings
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.services.context7 import Context7Service
from rubygemdb.services.prune import PruneService
from rubygemdb.services.stack import StackService
from rubygemdb.storage.sqlite_storage import SQLiteStorage
from rubygemdb.storage.json_storage import JSONStorage

console = Console()
logging.basicConfig(level=logging.INFO, format="%(message)s")

import re  # noqa: E402

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
    
    console.print("[bold magenta]--- PHASE 2: Heuristic & LLM Classification ---[/bold magenta]")
    
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

    if getattr(args, "embed", False):
        console.print("\n[bold cyan]--- PHASE 3: Vector Embeddings Generation ---[/bold cyan]")
        try:
            from rubygemdb.agent import TxtaiAgent
            agent = TxtaiAgent()
            agent.build_index()
            console.print("[green]Embeddings vectorization complete![/green]")
        except Exception as e:
            console.print(f"[red]Error during embeddings generation: {e}[/red]")


def _load_prune_list(path: str) -> list:
    """Load a plain-text prune list: one gem name per line, '#' comments allowed."""
    names = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(line)
    return names


def run_reclassify(args):
    """Approval-gated prune + fresh-start reclassification under the 21-category taxonomy."""
    storage = SQLiteStorage(rubygems_service=RubyGemsService(), context7_service=Context7Service())

    # 1. Back up the database (fresh start is destructive).
    backup_path = f"{settings.sqlite_db_file}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if settings.sqlite_db_file.exists():
        shutil.copy2(settings.sqlite_db_file, backup_path)
        console.print(f"[green]Database backed up to {backup_path}[/green]")

    # 2. Prune report (approval-gated; nothing removed without an explicit choice).
    inventory = storage.get_all_inventory_gems()
    console.print(f"[cyan]Building prune report for {len(inventory)} inventory gems...[/cyan]")
    pruner = PruneService(rubygems_service=storage.rubygems)
    report = pruner.build_report(inventory)
    report_path = pruner.write_report(report)
    console.print(f"[cyan]Prune report written to {report_path}[/cyan]")
    console.print(f"  Stale gems: {len(report.stale)}, overlap prunables: {len(report.overlaps)}")

    if args.prune_list:
        # Explicit subset approval: prune exactly the names in the file.
        inventory_names = {g["name"] for g in inventory}
        requested = _load_prune_list(args.prune_list)
        unknown = [n for n in requested if n not in inventory_names]
        if unknown:
            console.print(f"[yellow]Not in inventory (ignored): {', '.join(unknown)}[/yellow]")
        prune_names = [n for n in requested if n in inventory_names]
        if prune_names:
            for name in prune_names:
                storage.delete_gem(name)
            console.print(f"[red]Pruned {len(prune_names)} gems from --prune-list: {', '.join(prune_names)}[/red]")
        else:
            console.print("[yellow]--prune-list matched no inventory gems; nothing pruned.[/yellow]")
    elif report.all_names:
        apply_prune = args.apply_prune or Confirm.ask(
            f"Remove all {len(report.all_names)} pruned gems listed in the report? "
            "(use --prune-list for a subset)", default=False
        )
        if apply_prune:
            for name in report.all_names:
                storage.delete_gem(name)
            console.print(f"[red]Pruned {len(report.all_names)} gems from the inventory.[/red]")

    inventory = storage.get_all_inventory_gems()

    # 3. Fresh start: wipe classified_gems table, classified_gems.json, txtai index.
    storage.wipe_classified_gems()
    if settings.classified_gems_file.exists():
        settings.classified_gems_file.unlink()
        console.print("[yellow]Wiped classified_gems.json[/yellow]")
    if settings.txtai_dir.exists():
        shutil.rmtree(settings.txtai_dir)
        console.print("[yellow]Wiped txtai index directory[/yellow]")
    console.print("[yellow]Wiped classified_gems table[/yellow]")

    # 4. Re-classify every remaining inventory gem.
    rg_service = storage.rubygems
    llm_service = LLMService()
    classifier = GemClassifier(rg_service, llm_service)

    results = []
    categorized = defaultdict(list)
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), TaskProgressColumn(), TimeRemainingColumn(), console=console, transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Reclassifying gems...[/cyan]", total=len(inventory))
        for gem in inventory:
            name = gem["name"]
            progress.update(task, description=f"[cyan]Reclassifying [bold]{name}[/bold]...")
            gem_entry = classifier.classify(
                name,
                homepage=gem.get("homepage"),
                source_code_uri=gem.get("source_code_uri"),
                context7_id=gem.get("context7_id"),
            )
            results.append(gem_entry)
            categorized[gem_entry.classification.primary].append(gem_entry.model_dump())
            progress.advance(task)

    # 5. Save to SQLite, regenerate classified_gems.json from SQLite.
    storage.save_classified_gems(results)
    JSONStorage().save_classified_gems(storage.load_classified_gems())

    # 6. Per-category YAML outputs.
    os.makedirs(args.out, exist_ok=True)
    for cat, items in categorized.items():
        with open(f"{args.out}/{cat}.yaml", "w") as f:
            yaml.dump(items, f, sort_keys=False)

    table = Table(title="Reclassification Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="magenta", justify="right")
    for cat in sorted(categorized.keys()):
        table.add_row(cat, str(len(categorized[cat])))
    console.print(table)
    console.print(f"[green]Reclassified {len(results)} gems. YAML reports saved to {args.out}/[/green]")

    # 7. Rebuild the txtai index (skipped with a warning if ollama is down).
    if getattr(args, "embed", False):
        console.print("\n[bold cyan]--- Rebuilding txtai index (layer-aware) ---[/bold cyan]")
        try:
            from rubygemdb.agent import TxtaiAgent
            TxtaiAgent().build_index()
            console.print("[green]txtai index rebuilt![/green]")
        except Exception as e:
            console.print(f"[red]Error rebuilding txtai index (is ollama up?): {e}[/red]")


def run_stack(args):
    """Turn a context query into a layered gem-stack manifest plus a Gemfile snippet."""
    storage = SQLiteStorage(rubygems_service=RubyGemsService(), context7_service=Context7Service())
    service = StackService(storage=storage)
    manifest = service.build_stack(args.query)

    manifest_yaml = StackService.render_manifest_yaml(manifest)
    gemfile = StackService.render_gemfile(manifest)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "stack-manifest.yaml"), "w") as f:
            f.write(manifest_yaml)
        with open(os.path.join(args.out, "Gemfile"), "w") as f:
            f.write(gemfile)
        console.print(f"[green]Wrote {args.out}/stack-manifest.yaml and {args.out}/Gemfile[/green]")

    console.print(manifest_yaml)
    console.print("\n[bold cyan]Gemfile[/bold cyan]")
    console.print(gemfile)


def run_cli():
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--cuda", action="store_true", default=argparse.SUPPRESS, help="Force enable CUDA acceleration")
    parent_parser.add_argument("--cpu", action="store_true", default=argparse.SUPPRESS, help="Force CPU-only execution")

    parser = argparse.ArgumentParser(description="Ruby Gem Classifier", parents=[parent_parser])
    subparsers = parser.add_subparsers(dest="command", required=True, help="Command to run")

    # Unified Process Subcommand
    process_parser = subparsers.add_parser("process", parents=[parent_parser], help="Verify metadata and classify gems (Combined Pipeline)")
    process_parser.add_argument("csv", nargs="?", help="Optional path to initial gems inventory CSV")
    process_parser.add_argument("--out", default="output", help="Output directory for YAML files")
    process_parser.add_argument("--skip-verify", action="store_true", help="Skip Phase 1 metadata verification and go straight to Phase 2")
    process_parser.add_argument("--embed", action="store_true", help="Run the txtai embedding process to vectorize the database")

    # Reclassify Subcommand
    reclassify_parser = subparsers.add_parser("reclassify", parents=[parent_parser], help="Prune report + fresh-start reclassification under the 21-category taxonomy")
    reclassify_parser.add_argument("--apply-prune", action="store_true", help="Apply the full prune report (all stale/overlapping gems) without asking")
    reclassify_parser.add_argument("--prune-list", default=None, help="Path to a text file of gem names to prune (one per line, '#' comments) — subset approval; overrides --apply-prune")
    reclassify_parser.add_argument("--out", default="output", help="Output directory for per-category YAML files")
    reclassify_parser.add_argument("--embed", action="store_true", help="Rebuild the txtai index after reclassification")

    # Stack Subcommand
    stack_parser = subparsers.add_parser("stack", parents=[parent_parser], help="Turn a context query into a layered gem-stack manifest + Gemfile snippet")
    stack_parser.add_argument("query", help="Context query, e.g. 'CLI data pipeline tool'")
    stack_parser.add_argument("--out", default=None, help="Optional directory to write stack-manifest.yaml and Gemfile")

    args = parser.parse_args()

    if getattr(args, "cuda", False):
        settings.cuda_enabled = True
    elif getattr(args, "cpu", False):
        settings.cuda_enabled = False

    if args.command == "process":
        run_process(args)
    elif args.command == "reclassify":
        run_reclassify(args)
    elif args.command == "stack":
        run_stack(args)

if __name__ == "__main__":
    run_cli()
