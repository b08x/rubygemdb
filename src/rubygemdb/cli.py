import argparse
import yaml  # type: ignore
import os
import shutil
import sys
import logging
import requests
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table
from rich.prompt import Confirm, Prompt

from rubygemdb.core.config import settings
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService
from rubygemdb.services.classifier import GemClassifier
from rubygemdb.services.context7 import Context7Service
from rubygemdb.services.prune import (
    PruneCandidate,
    PruneService,
    parse_prune_candidates,
    parse_prune_file,
)
from rubygemdb.services.stack import StackService
from rubygemdb.services.embedding_cache import EmbeddingCache
from rubygemdb.services.import_stars_pipeline import ImportStarsPipeline
from rubygemdb.ui.selector import select_gems_to_prune
from rubygemdb.services.tracing import flush_tracing
from rubygemdb.storage.sqlite_storage import SQLiteStorage
from rubygemdb.storage.json_storage import JSONStorage

console = Console()
logging.basicConfig(level=logging.INFO, format="%(message)s")

_load_prune_list = parse_prune_file

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

    backend = getattr(args, "backend", "auto")

    if args.prune_list:
        # Explicit subset approval: prune exactly the names in the file.
        inventory_names = {g["name"] for g in inventory}
        requested = parse_prune_file(args.prune_list)
        unknown = [n for n in requested if n not in inventory_names]
        if unknown:
            preview = ", ".join(unknown[:5]) + ("..." if len(unknown) > 5 else "")
            console.print(f"[yellow]{len(unknown)} gem(s) in --prune-list not in inventory (ignored: {preview})[/yellow]")
        prune_names = [n for n in requested if n in inventory_names]
        if prune_names:
            for name in prune_names:
                storage.delete_gem(name)
            console.print(f"[red]Pruned {len(prune_names)} gems from --prune-list: {', '.join(prune_names)}[/red]")
        else:
            console.print("[yellow]--prune-list matched no inventory gems; nothing pruned.[/yellow]")
    elif getattr(args, "select_prune", False):
        candidates = report.stale + report.overlaps
        selected = select_gems_to_prune(candidates, header="Select gems to prune before reclassification:", backend=backend)
        if selected:
            for name in selected:
                storage.delete_gem(name)
            console.print(f"[red]Pruned {len(selected)} gems via multi-selector: {', '.join(selected)}[/red]")
        else:
            console.print("[yellow]No gems selected; skipping prune.[/yellow]")
    elif report.all_names:
        if args.apply_prune:
            for name in report.all_names:
                storage.delete_gem(name)
            console.print(f"[red]Pruned {len(report.all_names)} gems from the inventory.[/red]")
        elif sys.stdin.isatty():
            choice = Prompt.ask(
                f"Prune candidates found ({len(report.all_names)} gems). Action?",
                choices=["all", "select", "none"],
                default="select",
            )
            if choice == "all":
                for name in report.all_names:
                    storage.delete_gem(name)
                console.print(f"[red]Pruned {len(report.all_names)} gems from the inventory.[/red]")
            elif choice == "select":
                candidates = report.stale + report.overlaps
                selected = select_gems_to_prune(candidates, header="Select gems to prune before reclassification:", backend=backend)
                if selected:
                    for name in selected:
                        storage.delete_gem(name)
                    console.print(f"[red]Pruned {len(selected)} gems via multi-selector: {', '.join(selected)}[/red]")
                else:
                    console.print("[yellow]No gems selected; skipping prune.[/yellow]")

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

    if getattr(args, "clear_embeddings_cache", False):
        cache = EmbeddingCache()
        deleted = cache.clear(vacuum=True)
        console.print(f"[yellow]Wiped and vacuumed embedding cache ({deleted:,} vectors cleared)[/yellow]")

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


def run_build_indexer(args):
    """Build or rebuild the GraphRAG container indexer image."""
    from rubygemdb.services.container_runner import ContainerRunner
    runner = ContainerRunner()
    console.print(f"[bold cyan]Building indexer container image using {runner.runtime}...[/bold cyan]")
    runner.build_image(progress_callback=lambda msg: console.print(f"[dim]{msg}[/dim]"))
    console.print("[bold green]Successfully built rubygemdb-indexer:latest container image![/bold green]")


def run_stack(args):
    """Turn a context query into a layered gem-stack manifest plus a Gemfile snippet."""
    storage = SQLiteStorage(rubygems_service=RubyGemsService(), context7_service=Context7Service())
    service = StackService(storage=storage)
    max_gems = getattr(args, "max_gems", 10)
    manifest = service.build_stack(args.query, max_gems=max_gems)

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


def _format_bytes(num_bytes: int) -> str:
    n = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024.0:
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.2f} PB"


def run_cache(args):
    """Manage the universal vector embedding cache (stats, clear, prune)."""
    cache = EmbeddingCache()

    if args.cache_command == "stats":
        stats = cache.stats(model=args.model)
        table = Table(title="Universal Embedding Cache Statistics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        table.add_row("Database File", stats["db_file"])
        table.add_row("Total Vectors", f"{stats['total_vectors']:,}")
        table.add_row("Approx Vector Bytes", _format_bytes(stats["approx_vector_bytes"]))
        table.add_row("DB File Size", _format_bytes(stats["db_file_bytes"]))
        table.add_row("WAL File Size", _format_bytes(stats["wal_file_bytes"]))
        table.add_row("Total Disk Footprint", _format_bytes(stats["total_disk_bytes"]))
        table.add_row("Models Cached", ", ".join(stats["models"]) if stats["models"] else "None")

        console.print(table)

    elif args.cache_command == "clear":
        vacuum = not getattr(args, "no_vacuum", False)
        deleted = cache.clear(model=args.model, vacuum=vacuum)
        target = f"for model '{args.model}'" if args.model else "across all models"
        console.print(f"[green]Cleared {deleted:,} vector(s) {target} from embedding cache.[/green]")
        if vacuum:
            console.print("[cyan]Executed SQLite VACUUM to reclaim host disk space.[/cyan]")

    elif args.cache_command == "prune":
        vacuum = not getattr(args, "no_vacuum", False)
        res = cache.prune(
            max_age_days=args.max_age_days,
            max_entries=args.max_entries,
            vacuum=vacuum,
        )
        table = Table(title="Embedding Cache Prune Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        table.add_row("Expired Vectors Deleted", f"{res['deleted_expired']:,}")
        table.add_row("Capacity Vectors Trimmed", f"{res['deleted_capacity']:,}")
        table.add_row("Total Vectors Deleted", f"{res['total_deleted']:,}")
        table.add_row("Disk Reclaimed (VACUUM)", "Yes" if res["vacuumed"] else "No")

        console.print(table)


def run_prune(args):
    """Audit and selectively prune stale or overlapping gems from the inventory."""
    storage = SQLiteStorage()
    inventory = storage.get_all_inventory_gems()
    backend = getattr(args, "backend", "auto")
    file_path = getattr(args, "prune_list", None) or getattr(args, "file", None)

    candidate_map: dict[str, PruneCandidate] = {}
    gems_to_prune: list[str] = []

    if file_path:
        console.print(f"\n[bold magenta]--- RubyGemDB: Pruning from File '{file_path}' ---[/bold magenta]")
        file_candidates = parse_prune_candidates(file_path)
        inventory_names = {g["name"] for g in inventory}
        valid_candidates = [c for c in file_candidates if c.name in inventory_names]
        unknown = [c.name for c in file_candidates if c.name not in inventory_names]
        if unknown:
            preview = ", ".join(unknown[:5]) + ("..." if len(unknown) > 5 else "")
            console.print(f"[yellow]{len(unknown)} gem(s) in '{file_path}' not in inventory (ignored: {preview})[/yellow]")
        if not valid_candidates:
            console.print(f"[yellow]'{file_path}' matched no gems in the inventory; nothing to prune.[/yellow]")
            return

        candidate_map = {c.name: c for c in valid_candidates}

        if getattr(args, "all", False):
            gems_to_prune = [c.name for c in valid_candidates]
        elif getattr(args, "select", False) or sys.stdin.isatty():
            gems_to_prune = select_gems_to_prune(
                valid_candidates,
                header=f"Select gems from '{file_path}' to prune:",
                backend=backend,
            )
        else:
            gems_to_prune = [c.name for c in valid_candidates]

    else:
        pruner = PruneService(rubygems_service=storage.rubygems)
        report = pruner.build_report(inventory)

        console.print("\n[bold magenta]--- RubyGemDB: Prune Catalog Audit ---[/bold magenta]")
        console.print(f"Inventory: {len(inventory)} gems | Stale: {len(report.stale)} | Overlap prunables: {len(report.overlaps)}\n")

        if not report.all_names:
            console.print("[green]No prune candidates found in the catalog![/green]")
            return

        candidates = report.stale + report.overlaps
        candidate_map = {c.name: c for c in candidates}

        if getattr(args, "all", False):
            gems_to_prune = report.all_names
        else:
            # Default / --select: interactive multi-selector
            gems_to_prune = select_gems_to_prune(
                candidates,
                header="Select gems to prune (Space to toggle, Enter to confirm, Esc to abort):",
                backend=backend,
            )

    if getattr(args, "output_list", None) and gems_to_prune:
        with open(args.output_list, "w") as f:
            for name in sorted(gems_to_prune):
                f.write(f"{name}\n")
        console.print(f"[green]Saved {len(gems_to_prune)} selected gem(s) to '{args.output_list}'[/green]")

    if not gems_to_prune:
        console.print("[yellow]No gems selected for pruning.[/yellow]")
        return

    table = Table(title=f"Prune Target Gems ({'DRY RUN' if args.dry_run else 'ACTIVE'})")
    table.add_column("Gem Name", style="red")
    table.add_column("Reason", style="cyan")
    for name in gems_to_prune:
        c = candidate_map.get(name)
        detail_str = f"{c.reason} ({c.detail})" if c and c.detail else (c.reason if c and c.reason else "Prune candidate")
        table.add_row(name, detail_str)
    console.print(table)

    if getattr(args, "dry_run", False):
        console.print(f"[yellow][Dry Run] Would prune {len(gems_to_prune)} gems. Database unmodified.[/yellow]")
        return

    # Execute deletion
    for name in gems_to_prune:
        storage.delete_gem(name)
    console.print(f"[red]Successfully pruned {len(gems_to_prune)} gems from the catalog.[/red]")


def run_import_stars(args):
    """Import GitHub starred Ruby repositories into RubyGemDB."""
    console.print("\n[bold magenta]--- RubyGemDB: Import GitHub Starred Gems ---[/bold magenta]")
    dry_run_label = " [yellow](DRY RUN)[/yellow]" if args.dry_run else ""
    console.print(f"User: [cyan]@{args.user}[/cyan] | Language: [cyan]{args.language}[/cyan]{dry_run_label}\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Connecting to GitHub...", total=100)

        def progress_cb(msg: str, curr: int, total: int):
            progress.update(task, description=f"[cyan]{msg}", completed=curr, total=max(total, 1))

        pipeline = ImportStarsPipeline(
            user=args.user,
            language=args.language,
            dry_run=args.dry_run,
            classify=args.classify,
            embed=args.embed,
            apply_prune=args.apply_prune,
            select_prune=getattr(args, "select_prune", False),
            prune_list=args.prune_list,
            backend=getattr(args, "backend", "auto"),
            out_dir=args.out,
            progress_callback=progress_cb,
        )
        res = pipeline.run()

    # Summary table
    title = f"Import Summary for @{args.user}{' (DRY RUN)' if args.dry_run else ''}"
    summary_table = Table(title=title)
    summary_table.add_column("Category / Metric", style="cyan")
    summary_table.add_column("Count", style="magenta", justify="right")

    summary_table.add_row("Repositories Discovered", str(res["discovered_count"]))
    summary_table.add_row("RubyGems Resolved", str(len(res["resolved_gems"])))
    summary_table.add_row("Non-Gem Repositories Skipped", str(len(res["skipped_repos"])))
    summary_table.add_row("New Gems Added to Inventory", str(len(res["new_gems"])))
    summary_table.add_row("Existing Gems Enriched", str(len(res["enriched_gems"])))
    summary_table.add_row("Existing Gems Unchanged", str(len(res["unchanged_gems"])))
    if res.get("classified_gems"):
        summary_table.add_row("Gems Classified into Taxonomy", str(len(res["classified_gems"])))
    summary_table.add_row("Stale Prune Candidates", str(len(res["prune_report"].stale)))
    summary_table.add_row("Overlap Prune Candidates", str(len(res["prune_report"].overlaps)))
    summary_table.add_row("Gems Pruned", str(len(res["pruned_gems"])))

    console.print(summary_table)

    if res["new_gems"]:
        new_table = Table(title="Sample New Gems & Inferred GenAI Tooling Uses")
        new_table.add_column("Gem Name", style="green")
        new_table.add_column("Version", style="white")
        new_table.add_column("Inferred GenAI Use", style="cyan")
        for g in res["new_gems"][:15]:
            new_table.add_row(g["name"], g.get("version") or "-", g.get("genai_uses") or "-")
        if len(res["new_gems"]) > 15:
            new_table.add_row(f"... and {len(res['new_gems']) - 15} more", "", "")
        console.print(new_table)

    if res.get("prune_report_file"):
        console.print(f"\n[cyan]Audit prune report written to: {res['prune_report_file']}[/cyan]")
    elif args.dry_run:
        console.print(f"\n[yellow][Dry Run] Prune audit identified {len(res['prune_report'].all_names)} candidates for review.[/yellow]")

    if args.dry_run:
        console.print("[yellow]Dry run completed. No changes written to database or disk.[/yellow]")
    else:
        console.print("[green]Import process completed successfully![/green]")


def run_install_skill(args):
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent.parent
    install_script = repo_root / ".skills" / "rubygemdb" / "install.py"
    if not install_script.exists():
        console.print(f"[red]Installer script not found at {install_script}[/red]")
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("rubygemdb_skill_install", install_script)
    if spec is None or spec.loader is None:
        console.print(f"[red]Cannot load installer from {install_script}[/red]")
        sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    target_path = Path(args.target)
    dest = mod.install_skill(target_dir=target_path, link=args.link)
    action = "Linked" if args.link else "Installed"
    console.print(f"[green]{action} rubygemdb skill to {dest}[/green]")


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
    reclassify_parser.add_argument("--select-prune", action="store_true", help="Launch interactive multi-selector to choose which candidates to prune before reclassifying")
    reclassify_parser.add_argument("--prune-list", default=None, help="Path to a text file or markdown report of gem names to prune — subset approval; overrides --apply-prune")
    reclassify_parser.add_argument("--backend", choices=["auto", "gum", "textual"], default="auto", help="Multi-selector UI engine (default: auto)")
    reclassify_parser.add_argument("--out", default="output", help="Output directory for per-category YAML files")
    reclassify_parser.add_argument("--embed", action="store_true", help="Rebuild the txtai index after reclassification")
    reclassify_parser.add_argument("--clear-embeddings-cache", action="store_true", help="Wipe and vacuum the universal vector embedding cache alongside classified data")

    # Stack Subcommand
    stack_parser = subparsers.add_parser("stack", parents=[parent_parser], help="Turn a context query into a layered gem-stack manifest + Gemfile snippet")
    stack_parser.add_argument("query", help="Context query, e.g. 'CLI data pipeline tool'")
    stack_parser.add_argument("--max-gems", type=int, default=10, help="Maximum number of gems to include in the curated stack (default: 10)")
    stack_parser.add_argument("--out", default=None, help="Optional directory to write stack-manifest.yaml and Gemfile")

    # Build Indexer Subcommand
    subparsers.add_parser("build-indexer", parents=[parent_parser], help="Build or rebuild the GraphRAG container indexer image")

    # Cache Subcommand
    cache_parser = subparsers.add_parser("cache", parents=[parent_parser], help="Manage the universal vector embedding cache")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True, help="Cache action to execute")

    # Cache Stats
    cache_stats_parser = cache_subparsers.add_parser("stats", parents=[parent_parser], help="Display embedding cache statistics and disk usage")
    cache_stats_parser.add_argument("--model", default=None, help="Filter stats by embedding model name")

    # Cache Clear
    cache_clear_parser = cache_subparsers.add_parser("clear", parents=[parent_parser], help="Clear entries from the embedding cache and reclaim disk space")
    cache_clear_parser.add_argument("--model", default=None, help="Clear only entries for a specific model")
    cache_clear_parser.add_argument("--no-vacuum", action="store_true", help="Skip SQLite VACUUM disk reclamation after clearing")

    # Cache Prune
    cache_prune_parser = cache_subparsers.add_parser("prune", parents=[parent_parser], help="Prune expired or excess entries from the embedding cache")
    cache_prune_parser.add_argument("--max-age-days", type=int, default=None, help=f"Purge vectors older than N days (default: {settings.embeddings_cache_ttl_days})")
    cache_prune_parser.add_argument("--max-entries", type=int, default=None, help=f"Cap total vectors, evicting oldest (default: {settings.embeddings_cache_max_entries})")
    cache_prune_parser.add_argument("--no-vacuum", action="store_true", help="Skip SQLite VACUUM disk reclamation")

    # Prune Subcommand
    prune_parser = subparsers.add_parser("prune", parents=[parent_parser], help="Audit and selectively prune stale or overlapping gems")
    prune_parser.add_argument("file", nargs="?", default=None, help="Optional path to prune list file or markdown prune report (.md, .txt)")
    prune_parser.add_argument("--all", action="store_true", help="Prune all candidate gems without selection prompt")
    prune_parser.add_argument("--select", action="store_true", help="Launch interactive multi-selector to choose gems to prune (default in interactive terminal)")
    prune_parser.add_argument("--prune-list", default=None, help="Path to a text file or markdown report of gem names to prune")
    prune_parser.add_argument("--output-list", default=None, help="Save selected gem names to a text file compatible with --prune-list")
    prune_parser.add_argument("--dry-run", action="store_true", help="Simulate pruning and report selected gems without deleting from database")
    prune_parser.add_argument("--backend", choices=["auto", "gum", "textual"], default="auto", help="Multi-selector UI engine (default: auto)")

    # Import Stars Subcommand
    import_stars_parser = subparsers.add_parser("import-stars", parents=[parent_parser], help="Import GitHub starred Ruby repositories into RubyGemDB")
    import_stars_parser.add_argument("--user", default="b08x", help="GitHub username to fetch starred repositories for (default: b08x)")
    import_stars_parser.add_argument("--language", default="Ruby", help="Filter starred repositories by primary language (default: Ruby)")
    import_stars_parser.add_argument("--dry-run", action="store_true", help="Report discovered gems, matched gems, enriched records, and prune candidates without modifying SQLite or disk files")
    import_stars_parser.add_argument("--classify", action="store_true", help="Classify imported gems into the 21 architectural categories and write YAML reports")
    import_stars_parser.add_argument("--embed", action="store_true", help="Rebuild the txtai vector index after importing/classifying")
    import_stars_parser.add_argument("--apply-prune", action="store_true", help="Apply the full prune report (all stale and overlapping gems) without asking")
    import_stars_parser.add_argument("--select-prune", action="store_true", help="Launch interactive multi-selector to choose which audit candidates to prune")
    import_stars_parser.add_argument("--prune-list", default=None, help="Path to a text file or markdown report of gem names to prune — subset approval; overrides --apply-prune")
    import_stars_parser.add_argument("--backend", choices=["auto", "gum", "textual"], default="auto", help="Multi-selector UI engine (default: auto)")
    import_stars_parser.add_argument("--out", default="output", help="Output directory for YAML files (when --classify is passed)")

    # Install Skill Subcommand
    install_skill_parser = subparsers.add_parser("install-skill", parents=[parent_parser], help="Deploy the rubygemdb skill to ~/.agents/skills")
    install_skill_parser.add_argument("--target", default=str(Path.home() / ".agents" / "skills"), help="Target directory for installed skills (default: ~/.agents/skills)")
    install_skill_parser.add_argument("--link", action="store_true", help="Symlink the skill directory instead of copying")

    args = parser.parse_args()

    if getattr(args, "cuda", False):
        settings.cuda_enabled = True
    elif getattr(args, "cpu", False):
        settings.cuda_enabled = False

    try:
        if args.command == "process":
            run_process(args)
        elif args.command == "reclassify":
            run_reclassify(args)
        elif args.command == "stack":
            run_stack(args)
        elif args.command == "build-indexer":
            run_build_indexer(args)
        elif args.command == "cache":
            run_cache(args)
        elif args.command == "prune":
            run_prune(args)
        elif args.command == "import-stars":
            run_import_stars(args)
        elif args.command == "install-skill":
            run_install_skill(args)
    finally:
        flush_tracing()

if __name__ == "__main__":
    run_cli()
