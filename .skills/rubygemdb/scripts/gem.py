#!/usr/bin/env python
"""Agent-facing CLI over the RubyGemDB gem knowledge base.

Run inside the rubygemdb project environment:

    uv run --project /home/b08x/WorkspaceV3/rubygemdb \
        python ~/.agents/skills/rubygemdb/scripts/gem.py <subcommand> [args]

Subcommands:
    search <query> [--limit N] [--phase P] [--category C] [--vector] [--expand] [--rerank] [--json]
    info <name> [--json]
    add <name> [<name> ...]          # fetches from rubygems.org + classifies (network)
    remove <name> [<name> ...]
    prefer <name> [<name> ...]       # mark as user-preferred (boosted in search/stack)
    unprefer <name> [<name> ...]
    preferred                        # list preferred gems
    stack <query> [--max-gems N] [--out DIR]  # layered stack manifest + Gemfile snippet
    stats [--json]                   # counts per layer and category
    categories [slug] [--json]       # the 21-category taxonomy and sub-category facets
    graphrag build <gem> [...] [--intention TEXT] [--session-id ID] [--force] [--json]
    graphrag query <query> --gems <gem> [...] [--intention TEXT] [--session-id ID] [--top-k N] [--json]
    sessions list [--limit N] [--json]
    sessions show <session_id> [--json]
    sessions export <session_id> [--format markdown|json] [--out FILE]
    sessions delete <session_id> [--json]

The database is data/rubygemdb.sqlite inside the rubygemdb repo; all writes
(add/remove) keep the gem_fts keyword index in sync.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any

try:
    from rubygemdb.core.config import settings
    from rubygemdb.models.categories import CATEGORIES, CATEGORY_BY_SLUG, LAYERS
    from rubygemdb.models.phases import phase_for
    from rubygemdb.services.hybrid_search import build_fts_index, hybrid_search
    from rubygemdb.storage.sqlite_storage import SQLiteStorage
except ImportError as exc:  # pragma: no cover - wrong-environment guard
    sys.stderr.write(
        f"rubygemdb is not importable ({exc}).\n"
        "Run this script with the rubygemdb project environment, e.g.:\n"
        "  uv run --project /home/b08x/WorkspaceV3/rubygemdb "
        "python .skills/rubygemdb/scripts/gem.py --help\n"
    )
    sys.exit(2)

DB_PATH: str = str(settings.sqlite_db_file)
SESSION_DB_PATH: str = str(settings.session_db_file)


def _get_db_path() -> str:
    return globals().get("DB_PATH") or str(settings.sqlite_db_file)


def _get_session_db_path() -> str:
    return globals().get("SESSION_DB_PATH") or str(settings.session_db_file)


def _conn(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_session_storage(db_path: str | None = None) -> Any:
    from rubygemdb.storage.session_storage import SessionStorage

    path = db_path or _get_session_db_path()
    return SessionStorage(db_path=path)


def _ensure_schema_columns(db_path: str | None = None) -> None:
    """Migrate columns if an older database is missing them."""
    path = db_path or _get_db_path()
    if not os.path.exists(path):
        return
    conn = _conn(path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "inventory" in tables:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(inventory)")}
            if "preferred" not in columns:
                conn.execute("ALTER TABLE inventory ADD COLUMN preferred INTEGER DEFAULT 0")
            if "genai_uses" not in columns:
                conn.execute("ALTER TABLE inventory ADD COLUMN genai_uses TEXT")
        if "classified_gems" in tables:
            classified_columns = {row[1] for row in conn.execute("PRAGMA table_info(classified_gems)")}
            if "sub_categories" not in classified_columns:
                conn.execute("ALTER TABLE classified_gems ADD COLUMN sub_categories TEXT DEFAULT '[]'")
            if "genai_uses" not in classified_columns:
                conn.execute("ALTER TABLE classified_gems ADD COLUMN genai_uses TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


def _classify_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw) or {}
    except (ValueError, TypeError):
        return {}


def _hit(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": h.get("name"),
        "category": h.get("category"),
        "layer": h.get("layer"),
        "phase": h.get("phase"),
        "score": h.get("score"),
        "preferred": bool(h.get("preferred")),
        "description": (h.get("description") or "").strip(),
        "source_code_uri": h.get("source_code_uri"),
        "context7_id": h.get("context7_id"),
    }


def _confidence_map(names: list[str]) -> dict[str, float]:
    if not names:
        return {}
    placeholders = ", ".join("?" for _ in names)
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT name, classification FROM classified_gems WHERE name IN ({placeholders})",
            names,
        ).fetchall()
    finally:
        conn.close()
    return {
        row["name"]: _classify_json(row["classification"]).get("confidence", 0.0)
        for row in rows
    }


def cmd_search(args: argparse.Namespace) -> int:
    vector_search = None
    if getattr(args, "vector", False):
        from rubygemdb.agent import TxtaiAgent

        agent = TxtaiAgent()
        vector_search = agent._vector_search

    query_expander = None
    if getattr(args, "expand", False):
        from rubygemdb.services.query_expander import QueryExpander

        query_expander = QueryExpander(enabled=True)

    reranker = None
    if getattr(args, "rerank", False):
        from rubygemdb.services.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker(enabled=True)

    hits = hybrid_search(
        args.query,
        phase=args.phase,
        category=getattr(args, "category", None),
        limit=args.limit,
        vector_search=vector_search,
        db_path=_get_db_path(),
        query_expander=query_expander,
        reranker=reranker,
    )
    confidences = _confidence_map([h.get("name") for h in hits])
    results = []
    for h in hits:
        item = _hit(h)
        item["confidence"] = confidences.get(item["name"], 0.0)
        results.append(item)

    if args.json:
        print(json.dumps(results, indent=2))
    elif not results:
        print(f"No gems matched '{args.query}'.")
    else:
        print(f"Gem matches for '{args.query}':")
        for r in results:
            marker = "★ " if r["preferred"] else ""
            print(
                f"\n- {marker}{r['name']}  [{r['category']} | layer={r['layer']} | phase={r['phase']}"
                f" | conf={r['confidence']:.2f}]"
            )
            print(f"  {r['description'][:200]}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    conn = _conn()
    try:
        inv = conn.execute(
            "SELECT * FROM inventory WHERE name = ? COLLATE NOCASE", (args.name,)
        ).fetchone()
        cls = conn.execute(
            "SELECT * FROM classified_gems WHERE name = ? COLLATE NOCASE", (args.name,)
        ).fetchone()
    finally:
        conn.close()

    if inv is None:
        print(f"'{args.name}' is not in the inventory.")
        return 1

    classification = _classify_json(cls["classification"] if cls else None)

    sub_cats = classification.get("sub_categories")
    if not sub_cats and cls:
        raw_sub = cls["sub_categories"] if "sub_categories" in cls.keys() else None
        if raw_sub:
            try:
                sub_cats = json.loads(raw_sub)
            except Exception:
                sub_cats = []
    if not sub_cats:
        sub_cats = []

    genai_uses = None
    if cls and "genai_uses" in cls.keys() and cls["genai_uses"]:
        genai_uses = cls["genai_uses"]
    elif inv and "genai_uses" in inv.keys() and inv["genai_uses"]:
        genai_uses = inv["genai_uses"]

    record = {
        "name": inv["name"],
        "description": inv["description"],
        "category": classification.get("primary"),
        "sub_categories": sub_cats,
        "genai_uses": genai_uses,
        "confidence": classification.get("confidence", 0.0),
        "layer": CATEGORY_BY_SLUG[classification["primary"]].layer
        if classification.get("primary") in CATEGORY_BY_SLUG
        else None,
        "phase": phase_for(classification.get("primary")),
        "version": inv["version"],
        "homepage": inv["homepage"],
        "source_code_uri": inv["source_code_uri"],
        "context7_id": inv["context7_id"],
        "verified": bool(inv["verified"]),
        "role": _classify_json(cls["role"] if cls else None),
        "capabilities": json.loads(cls["capabilities"]) if cls and cls["capabilities"] else [],
        "risks": _classify_json(cls["risks"] if cls else None),
        "signals": _classify_json(cls["signals"] if cls else None),
        "dependencies": json.loads(cls["dependencies"]) if cls and cls["dependencies"] else [],
        "preferred": bool(inv["preferred"]),
    }

    if args.json:
        print(json.dumps(record, indent=2))
        return 0

    marker = "★ " if record["preferred"] else ""
    print(f"{marker}{record['name']} — {record['category']} (confidence {record['confidence']:.2f})")
    print(f"layer: {record['layer']}  phase: {record['phase']}  verified: {record['verified']}  preferred: {record['preferred']}")
    print(f"sub-categories: {', '.join(record['sub_categories']) or 'none'}")
    if record["genai_uses"]:
        print(f"genai-uses: {record['genai_uses']}")
    print(f"description: {record['description']}")
    print(f"homepage: {record['homepage']}")
    print(f"source: {record['source_code_uri']}")
    print(f"context7: {record['context7_id']}")
    if record["role"]:
        print(f"role: {record['role'].get('description', record['role'])}")
    if record["capabilities"]:
        print(f"capabilities: {', '.join(record['capabilities'])}")
    if record["risks"]:
        print(f"risks: {record['risks']}")
    if record["dependencies"]:
        print(f"runtime dependencies: {', '.join(record['dependencies'])}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    from rubygemdb.services.classifier import GemClassifier
    from rubygemdb.services.llm import LLMService
    from rubygemdb.services.rubygems import RubyGemsService

    rg_service = RubyGemsService()
    classifier = GemClassifier(rg_service, LLMService())
    storage = SQLiteStorage(db_path=_get_db_path(), rubygems_service=rg_service)

    failures = []
    for name in args.names:
        try:
            storage.add_gem_to_inventory(name)
            entry = classifier.classify(name)
            storage.save_classified_gems([entry])
            print(
                f"Added {entry.name}: {entry.classification.primary} "
                f"(confidence {entry.classification.confidence:.2f})"
            )
        except Exception as exc:
            failures.append(name)
            print(f"Failed to add {name}: {exc}")

    build_fts_index(_get_db_path())
    if failures:
        return 1
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    storage = SQLiteStorage(db_path=_get_db_path())
    failures = []
    for name in args.names:
        try:
            storage.delete_gem(name)
            print(f"Removed {name} from inventory and classified_gems.")
        except Exception as exc:
            failures.append(name)
            print(f"Failed to remove {name}: {exc}")

    build_fts_index(_get_db_path())
    if failures:
        return 1
    return 0


def cmd_prefer(args: argparse.Namespace) -> int:
    storage = SQLiteStorage(db_path=_get_db_path())
    storage.set_preferred(args.names, True)
    for name in args.names:
        print(f"{name} marked preferred.")
    return 0


def cmd_unprefer(args: argparse.Namespace) -> int:
    storage = SQLiteStorage(db_path=_get_db_path())
    storage.set_preferred(args.names, False)
    for name in args.names:
        print(f"{name} unmarked.")
    return 0


def cmd_preferred(_args: argparse.Namespace) -> int:
    storage = SQLiteStorage(db_path=_get_db_path())
    gems = storage.get_preferred()
    if not gems:
        print("No gems marked preferred yet. Use: gem.py prefer <name>")
        return 0
    for g in gems:
        print(f"★ {g['name']}  [{g['category']} | layer={g['layer']}]")
        if g.get("description"):
            print(f"  {g['description'][:160]}")
    return 0


def cmd_stack(args: argparse.Namespace) -> int:
    from rubygemdb.services.stack import StackService

    storage = SQLiteStorage(db_path=_get_db_path())
    service = StackService(storage=storage)
    manifest = service.build_stack(args.query, max_gems=args.max_gems)
    manifest_yaml = StackService.render_manifest_yaml(manifest)
    gemfile = StackService.render_gemfile(manifest)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        manifest_path = os.path.join(args.out, "stack-manifest.yaml")
        gemfile_path = os.path.join(args.out, "Gemfile")
        with open(manifest_path, "w") as f:
            f.write(manifest_yaml)
        with open(gemfile_path, "w") as f:
            f.write(gemfile)
        print(f"Wrote {manifest_path} and {gemfile_path}")
    else:
        print(manifest_yaml)
        print(gemfile)
    return 0


def _stats_python() -> dict[str, Any]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT c.classification FROM classified_gems c"
        ).fetchall()
    finally:
        conn.close()
    layers = {layer: 0 for layer in LAYERS}
    categories: dict[str, int] = {}
    for row in rows:
        primary = _classify_json(row["classification"]).get("primary")
        cat = CATEGORY_BY_SLUG.get(primary)
        if primary:
            categories[primary] = categories.get(primary, 0) + 1
        if cat:
            layers[cat.layer] += 1
    return {
        "total_classified": len(rows),
        "by_layer": layers,
        "by_category": dict(sorted(categories.items(), key=lambda kv: -kv[1])),
    }


def cmd_stats_impl(args: argparse.Namespace) -> int:
    stats = _stats_python()
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print(f"Classified gems: {stats['total_classified']}")
        print("By layer:")
        for layer, n in stats["by_layer"].items():
            print(f"  {layer}: {n}")
        print("By category:")
        for cat, n in stats["by_category"].items():
            print(f"  {cat}: {n}")
    return 0


def _format_category(record: dict[str, Any]) -> str:
    lines = [
        f"{record['slug']} — {record['label']}",
        f"layer: {record['layer']}  phase: {record['phase']}",
        f"description: {record['description']}",
        f"keywords: {', '.join(record['keywords'])}",
        f"base gems: {', '.join(record['base_gems'])}",
    ]
    if record.get("sub_categories"):
        lines.append("sub-categories:")
        for facet, examples in record["sub_categories"].items():
            ex_str = f" ({', '.join(examples)})" if examples else ""
            lines.append(f"  - {facet}{ex_str}")
    return "\n".join(lines)


def cmd_categories(args: argparse.Namespace) -> int:
    if args.slug:
        cat = CATEGORY_BY_SLUG.get(args.slug)
        if cat is None:
            print(f"Unknown category slug '{args.slug}'.")
            return 1
        record = {
            "slug": cat.slug,
            "label": cat.label,
            "layer": cat.layer,
            "phase": phase_for(cat.slug),
            "description": cat.description,
            "keywords": cat.keywords,
            "base_gems": cat.base_gems,
            "sub_categories": cat.sub_categories,
        }
        print(json.dumps(record, indent=2) if args.json else _format_category(record))
        return 0

    if args.json:
        print(json.dumps(
            [
                {
                    "slug": c.slug,
                    "label": c.label,
                    "layer": c.layer,
                    "phase": phase_for(c.slug),
                    "sub_categories": list(c.sub_categories.keys()),
                }
                for c in CATEGORIES
            ],
            indent=2,
        ))
    else:
        for layer in LAYERS:
            print(f"\n{layer} layer:")
            for c in CATEGORIES:
                if c.layer == layer:
                    facets_str = f" [facets: {', '.join(c.sub_categories.keys())}]" if c.sub_categories else ""
                    print(f"  {c.slug:<24} {c.label}  [phase: {phase_for(c.slug)}]{facets_str}")
    return 0


def cmd_graphrag_build(args: argparse.Namespace) -> int:
    import uuid
    from rubygemdb.services.graphrag import GraphRAGService

    sid = getattr(args, "session_id", None) or str(uuid.uuid4())
    intention = getattr(args, "intention", "") or ""
    gems = getattr(args, "gems", [])

    session_storage = _get_session_storage()
    session = session_storage.get_session(sid)
    if not session:
        session_storage.create_session(
            session_id=sid,
            active_gems=gems,
            intention=intention,
        )
    else:
        session_storage.update_session(
            sid,
            active_gems=gems,
            intention=intention or session.intention,
        )

    service = GraphRAGService()
    data = service.build_or_load_index(gems, force_rebuild=getattr(args, "force", False))

    res = {
        "session_id": sid,
        "gems": service.active_gems or gems,
        "hash": service.active_hash,
        "nodes_count": len(service.nodes_by_id),
        "edges_count": len(data.get("edges", [])),
        "status": "ready",
    }
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("GraphRAG index ready:")
        print(f"  session_id:  {res['session_id']}")
        print(f"  gems:        {', '.join(res['gems'])}")
        print(f"  hash:        {res['hash']}")
        print(f"  nodes:       {res['nodes_count']}")
        print(f"  edges:       {res['edges_count']}")
        print(f"  status:      {res['status']}")
    return 0


def cmd_graphrag_query(args: argparse.Namespace) -> int:
    import uuid
    from rubygemdb.services.graphrag import GraphRAGService

    sid = getattr(args, "session_id", None) or str(uuid.uuid4())
    intention = getattr(args, "intention", "") or ""
    gems = getattr(args, "gems", [])

    session_storage = _get_session_storage()
    session = session_storage.get_session(sid)
    if not session:
        session_storage.create_session(
            session_id=sid,
            active_gems=gems,
            intention=intention,
        )
    elif gems:
        session_storage.update_session(
            sid,
            active_gems=gems,
            intention=intention or session.intention,
        )

    service = GraphRAGService()
    service.build_or_load_index(gems)
    retrieval = service.retrieve(args.query, top_k=args.top_k)
    grounded_context = service.format_grounded_context(retrieval, intention=intention)
    citations = retrieval.get("citations", [])

    retrieved_node_ids: list[str] = []
    for n in retrieval.get("primary_nodes", []):
        if isinstance(n, dict) and "id" in n:
            retrieved_node_ids.append(str(n["id"]))
        elif isinstance(n, str):
            retrieved_node_ids.append(n)
    for n in retrieval.get("expanded_nodes", []):
        if isinstance(n, dict) and "id" in n:
            retrieved_node_ids.append(str(n["id"]))
        elif isinstance(n, str):
            retrieved_node_ids.append(n)

    session_storage.append_message(sid, role="user", content=args.query)
    session_storage.append_message(
        sid,
        role="agent",
        content=grounded_context,
        retrieved_nodes=retrieved_node_ids,
        citations=citations,
    )

    if args.json:
        payload = {
            "session_id": sid,
            "citations": citations,
            "grounded_context": grounded_context,
            "primary_nodes": retrieval.get("primary_nodes", []),
            "expanded_nodes": retrieval.get("expanded_nodes", []),
            "edges": retrieval.get("edges", []),
        }
        print(json.dumps(payload, indent=2))
    else:
        print(grounded_context)
        print(f"\nSession ID: {sid}")
    return 0


def cmd_sessions_list(args: argparse.Namespace) -> int:
    session_storage = _get_session_storage()
    sessions = session_storage.list_sessions(limit=args.limit)
    if args.json:
        print(json.dumps([s.model_dump() for s in sessions], indent=2))
    elif not sessions:
        print("No sessions found.")
    else:
        print(f"GraphRAG Sessions (showing up to {args.limit}):")
        for s in sessions:
            gems_str = f" [{', '.join(s.active_gems)}]" if s.active_gems else ""
            print(f"- {s.session_id} ({s.status}) - {s.title}{gems_str}")
            print(f"  updated: {s.updated_at} | intention: {s.intention or 'none'}")
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    session_storage = _get_session_storage()
    session = session_storage.get_session(args.session_id)
    if not session:
        print(f"Session '{args.session_id}' not found.")
        return 1

    messages = session_storage.get_messages(args.session_id)
    traces = session_storage.get_traces(args.session_id)

    if args.json:
        payload = {
            "session": session.model_dump(),
            "messages": [m.model_dump() for m in messages],
            "traces": [t.model_dump() for t in traces],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"Session: {session.session_id} ({session.status})")
        print(f"Title: {session.title}")
        print(f"Gems: {', '.join(session.active_gems) or 'none'}")
        print(f"Intention: {session.intention or 'none'}")
        print(f"Created: {session.created_at} | Updated: {session.updated_at}")
        print(f"\nMessages ({len(messages)}):")
        for m in messages:
            cites_str = f" [citations: {len(m.citations)}]" if m.citations else ""
            nodes_str = f" [nodes: {len(m.retrieved_nodes)}]" if m.retrieved_nodes else ""
            print(f"\n  [{m.role.upper()}] ({m.timestamp}){cites_str}{nodes_str}:")
            for line in m.content.splitlines():
                print(f"    {line}")
        if traces:
            print(f"\nTraces ({len(traces)}):")
            for t in traces:
                print(f"  [{t.timestamp}] {t.trace_text}")
    return 0


def cmd_sessions_export(args: argparse.Namespace) -> int:
    session_storage = _get_session_storage()
    try:
        md_text, json_data = session_storage.export_transcript(args.session_id, format=args.format)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    output_content = json.dumps(json_data, indent=2) if args.format == "json" else md_text

    if args.out:
        with open(args.out, "w") as f:
            f.write(output_content)
        print(f"Exported session {args.session_id} to {args.out}")
    else:
        print(output_content)
    return 0


def cmd_sessions_delete(args: argparse.Namespace) -> int:
    session_storage = _get_session_storage()
    success = session_storage.delete_session(args.session_id)
    if args.json:
        print(json.dumps({"session_id": args.session_id, "deleted": success}))
    elif success:
        print(f"Deleted session {args.session_id}.")
    else:
        print(f"Session {args.session_id} not found.")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RubyGemDB gem knowledge base CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="Keyword/hybrid search over the gem database")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--phase", choices=["skeleton", "input", "processing", "output"], default=None)
    p.add_argument("--category", default=None, help="Restrict search to category slug")
    p.add_argument("--vector", action="store_true", help="Also use txtai vector search (needs ollama)")
    p.add_argument("--expand", action="store_true", help="Expand query using local GGUF query expander")
    p.add_argument("--rerank", action="store_true", help="Rerank candidate pool using cross-encoder")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("info", help="Full record for one gem")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("add", help="Add and classify gems (fetches from rubygems.org)")
    p.add_argument("names", nargs="+")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("remove", help="Remove gems from the database")
    p.add_argument("names", nargs="+")
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("prefer", help="Mark gems as preferred (boosted in search and stack)")
    p.add_argument("names", nargs="+")
    p.set_defaults(fn=cmd_prefer)

    p = sub.add_parser("unprefer", help="Remove the preferred mark")
    p.add_argument("names", nargs="+")
    p.set_defaults(fn=cmd_unprefer)

    p = sub.add_parser("preferred", help="List preferred gems")
    p.set_defaults(fn=cmd_preferred)

    p = sub.add_parser("stack", help="Compose a layered gem stack for a context query")
    p.add_argument("query")
    p.add_argument("--max-gems", type=int, default=10, help="Maximum number of gems in the stack (default: 10)")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_stack)

    p = sub.add_parser("stats", help="Database counts by layer and category")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_stats_impl)

    p = sub.add_parser("categories", help="List the taxonomy; pass a slug for one category's detail")
    p.add_argument("slug", nargs="?", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_categories)

    # graphrag subparser
    p_gr = sub.add_parser("graphrag", help="Contextual GraphRAG gem index and retrieval")
    sub_gr = p_gr.add_subparsers(dest="graphrag_command", required=True)

    p_gr_build = sub_gr.add_parser("build", help="Build or load GraphRAG index for gems")
    p_gr_build.add_argument("gems", nargs="+", help="Gems to index")
    p_gr_build.add_argument("--intention", default="", help="Project intention or architectural context")
    p_gr_build.add_argument("--session-id", default=None, help="Session UUID")
    p_gr_build.add_argument("--force", action="store_true", help="Force rebuild index")
    p_gr_build.add_argument("--json", action="store_true")
    p_gr_build.set_defaults(fn=cmd_graphrag_build)

    p_gr_query = sub_gr.add_parser("query", help="Query GraphRAG index for grounded citations")
    p_gr_query.add_argument("query", help="Query text")
    p_gr_query.add_argument("--gems", nargs="+", required=True, help="Active gems to query")
    p_gr_query.add_argument("--intention", default="", help="Project intention or architectural context")
    p_gr_query.add_argument("--session-id", default=None, help="Session UUID")
    p_gr_query.add_argument("--top-k", type=int, default=5, help="Number of primary nodes to retrieve (default: 5)")
    p_gr_query.add_argument("--json", action="store_true")
    p_gr_query.set_defaults(fn=cmd_graphrag_query)

    # sessions subparser
    p_sess = sub.add_parser("sessions", help="Manage GraphRAG persistent sessions")
    sub_sess = p_sess.add_subparsers(dest="sessions_command", required=True)

    p_sess_list = sub_sess.add_parser("list", help="List persistent GraphRAG sessions")
    p_sess_list.add_argument("--limit", type=int, default=20, help="Max sessions to list (default: 20)")
    p_sess_list.add_argument("--json", action="store_true")
    p_sess_list.set_defaults(fn=cmd_sessions_list)

    p_sess_show = sub_sess.add_parser("show", help="Show session details, messages, and traces")
    p_sess_show.add_argument("session_id", help="Session UUID")
    p_sess_show.add_argument("--json", action="store_true")
    p_sess_show.set_defaults(fn=cmd_sessions_show)

    p_sess_export = sub_sess.add_parser("export", help="Export session transcript to markdown or json")
    p_sess_export.add_argument("session_id", help="Session UUID")
    p_sess_export.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Export format")
    p_sess_export.add_argument("--out", default=None, help="Output file path")
    p_sess_export.set_defaults(fn=cmd_sessions_export)

    p_sess_del = sub_sess.add_parser("delete", help="Delete a session")
    p_sess_del.add_argument("session_id", help="Session UUID")
    p_sess_del.add_argument("--json", action="store_true")
    p_sess_del.set_defaults(fn=cmd_sessions_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _ensure_schema_columns()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
