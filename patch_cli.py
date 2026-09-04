import sys

with open("src/rubygemdb/cli.py", "r") as f:
    content = f.read()

# Add arg
if "--embed" not in content:
    content = content.replace(
        'process_parser.add_argument("--skip-verify", action="store_true", help="Skip Phase 1 metadata verification and go straight to Phase 2")',
        'process_parser.add_argument("--skip-verify", action="store_true", help="Skip Phase 1 metadata verification and go straight to Phase 2")\n    process_parser.add_argument("--embed", action="store_true", help="Run the txtai embedding process to vectorize the database")'
    )

# Add execution block
exec_code = """
    if getattr(args, "embed", False):
        console.print("\\n[bold cyan]--- PHASE 3: Vector Embeddings Generation ---[/bold cyan]")
        try:
            from rubygemdb.agent import TxtaiAgent
            agent = TxtaiAgent()
            agent.build_index()
            console.print("[green]Embeddings vectorization complete![/green]")
        except Exception as e:
            console.print(f"[red]Error during embeddings generation: {e}[/red]")
"""

if "PHASE 3: Vector Embeddings" not in content:
    content = content.replace(
        'console.print(f"[green]All processes complete! Saved reports to {args.out}/[/green]")',
        'console.print(f"[green]All processes complete! Saved reports to {args.out}/[/green]")' + "\n" + exec_code
    )

with open("src/rubygemdb/cli.py", "w") as f:
    f.write(content)
