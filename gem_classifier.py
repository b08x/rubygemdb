#!/usr/bin/env python
import csv
import time
import json
import os
import requests
import yaml
from collections import defaultdict
from hashlib import sha256
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table
from rich.panel import Panel

# -----------------------------
# Config
# -----------------------------
RUBYGEMS_API_URL = "https://rubygems.org/api/v1/gems/{name}.json"
RATE_LIMIT_DELAY = 0.5
LLM_BATCH_SIZE = 10
LLM_RATE_DELAY = 0.5
CACHE_FILE = "gem_cache.json"
LLM_CACHE_FILE = "llm_cache.json"

console = Console()

# Set your Devstral endpoint + key via env
LLM_ENDPOINT = os.getenv("DEVSTRAL_ENDPOINT", "https://api.mistral.ai/v1/chat/completions")
LLM_API_KEY = os.getenv("MISSTRAL_API_KEY")
LLM_MODEL = os.getenv("DEVSTRAL_MODEL", "devstral-small")

# -----------------------------
# Cache helpers
# -----------------------------

def load_cache(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_cache(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


gem_cache = load_cache(CACHE_FILE)
llm_cache = load_cache(LLM_CACHE_FILE)

# -----------------------------
# RubyGems API
# -----------------------------

def fetch_gem_info(name, retries=3):
    if name in gem_cache:
        return gem_cache[name]

    for attempt in range(retries):
        try:
            r = requests.get(RUBYGEMS_API_URL.format(name=name), timeout=5)
            if r.status_code == 200:
                data = r.json()
                gem_cache[name] = data
                save_cache(CACHE_FILE, gem_cache)
                return data
        except Exception:
            time.sleep(1 * (attempt + 1))
    return None

# -----------------------------
# Heuristics
# -----------------------------

def classify_gem(name, info, group=None):
    lname = name.lower()

    def has(keys):
        return any(k in lname for k in keys)

    classification = {"primary": "application_capability", "secondary": None}
    signals = {"rails": False, "external_io": False, "native_ext": False}
    confidence = 0.6

    deps = [d["name"] for d in info.get("dependencies", {}).get("runtime", [])] if info else []

    if has(["active_support", "core_ext", "dry-"]):
        classification["primary"] = "runtime_substrate"
        confidence += 0.2

    elif "railties" in deps or has(["rails", "engine", "sidekiq"]):
        classification["primary"] = "framework_integration"
        signals["rails"] = True
        confidence += 0.3

    elif has(["http", "faraday", "aws", "google", "stripe", "grpc"]):
        classification["primary"] = "boundary_interface"
        signals["external_io"] = True
        confidence += 0.2

    elif has(["pundit", "auth", "jwt"]):
        classification["primary"] = "policy_enforcement"
        confidence += 0.2

    elif has(["sentry", "datadog", "newrelic", "log"]):
        classification["primary"] = "observability"
        signals["external_io"] = True
        confidence += 0.2

    elif group in ["development", "test"] or has(["rspec", "rubocop", "pry", "tty-"]):
        classification["primary"] = "developer_experience"
        confidence += 0.3

    if info and info.get("platform") not in (None, "ruby"):
        signals["native_ext"] = True

    return classification, signals, confidence, deps

# -----------------------------
# LLM integration (Devstral)
# -----------------------------

def prompt_hash(prompt):
    return sha256(prompt.encode()).hexdigest()


def build_prompt(name, info, deps):
    return f"""
Classify this Ruby gem into ONE category:
- runtime_substrate
- framework_integration
- boundary_interface
- application_capability
- policy_enforcement
- observability
- developer_experience
- build_delivery

Gem:
{name}
Description:
{info.get('info','') if info else ''}
Dependencies:
{', '.join(deps)}

Return JSON only:
{{"primary":"...","confidence":0.0}}
"""


def call_llm(prompt):
    key = prompt_hash(prompt)
    if key in llm_cache:
        return llm_cache[key]

    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0
    }

    try:
        r = requests.post(LLM_ENDPOINT, json=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            result = json.loads(text)
            llm_cache[key] = result
            save_cache(LLM_CACHE_FILE, llm_cache)
            return result
    except Exception:
        pass

    return None

# -----------------------------
# Scoring
# -----------------------------

def score_gem(classification, deps):
    if classification["primary"] == "runtime_substrate":
        inv = 5
    elif classification["primary"] == "framework_integration":
        inv = 4
    elif classification["primary"] == "boundary_interface":
        inv = 2
    elif classification["primary"] == "developer_experience":
        inv = 1
    else:
        inv = 3

    coupling = min(4, max(1, len(deps)//3 + 1))

    leak = "high" if classification["primary"] == "boundary_interface" else (
        "medium" if classification["primary"] == "framework_integration" else "low"
    )

    return {"invasiveness": inv, "coupling": coupling, "abstraction_leak": leak}

# -----------------------------
# Main pipeline
# -----------------------------

def process_csv(path, output_dir="output"):
    os.makedirs(output_dir, exist_ok=True)
    results = defaultdict(list)

    # Pre-count for progress bar
    with open(path) as f:
        total_rows = sum(1 for _ in csv.DictReader(f))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Classifying gems...", total=total_rows)

        with open(path) as f:
            reader = csv.DictReader(f)
            batch = []

            for row in reader:
                name = row.get("name") or row.get("gem")
                group = row.get("group")
                if not name:
                    progress.advance(task)
                    continue

                progress.update(task, description=f"[cyan]Analyzing [bold]{name}[/bold]...")
                info = fetch_gem_info(name)
                time.sleep(RATE_LIMIT_DELAY)

                classification, signals, conf, deps = classify_gem(name, info, group)

                # LLM fallback
                if conf < 0.7:
                    batch.append((name, info, deps, classification, signals))

                    if len(batch) >= LLM_BATCH_SIZE:
                        progress.update(task, description=f"[yellow]Processing LLM batch ({len(batch)} items)...")
                        process_batch(batch, results)
                        batch = []
                else:
                    finalize(name, classification, signals, deps, info, results)
                
                progress.advance(task)

            if batch:
                progress.update(task, description=f"[yellow]Processing final LLM batch ({len(batch)} items)...")
                process_batch(batch, results)

    for cat, gems in results.items():
        with open(f"{output_dir}/{cat}.yaml", "w") as f:
            yaml.dump(gems, f, sort_keys=False)
    
    return results


def process_batch(batch, results):
    for name, info, deps, classification, signals in batch:
        prompt = build_prompt(name, info, deps)
        llm = call_llm(prompt)
        time.sleep(LLM_RATE_DELAY)

        if llm and llm.get("confidence", 0) > 0.6:
            classification["primary"] = llm["primary"]

        finalize(name, classification, signals, deps, info, results)


def finalize(name, classification, signals, deps, info, results):
    entry = {
        "name": name,
        "classification": classification,
        "role": {
            "description": info.get("info") if info else "",
            "attaches_to": classification["primary"].split("_")[0]
        },
        "capabilities": [],
        "risks": score_gem(classification, deps),
        "signals": signals,
        "usage_patterns": [],
        "failure_modes": []
    }

    results[classification["primary"]].append(entry)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("csv")
    p.add_argument("--out", default="output")
    args = p.parse_args()

    console.print(Panel.fit(
        "[bold cyan]Ruby Gem Classifier[/bold cyan]\n"
        "[dim]Analyzing gems via RubyGems API & LLM heuristics[/dim]",
        border_style="blue"
    ))

    results = process_csv(args.csv, args.out)

    table = Table(title="Classification Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="magenta", justify="right")

    for cat in sorted(results.keys()):
        table.add_row(cat, str(len(results[cat])))

    console.print("\n")
    console.print(table)
