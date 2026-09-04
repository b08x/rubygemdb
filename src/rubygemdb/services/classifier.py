from typing import List, Tuple, Optional
from rubygemdb.models.gem import GemEntry, GemClassification, GemSignals, GemRisks
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.llm import LLMService

# Valid architectural categories (12-category system)
VALID_CATEGORIES = [
    "runtime_spine",
    "cli_terminal_ui",
    "storage_persistence",
    "async_networking_orchestration",
    "ai_nlp",
    "data_processing",
    "retrieval_similarity_fuzzy",
    "algorithms_knowledge_structures",
    "validation_types",
    "parsing_encoding",
    "debugging_introspection",
    "mcp_tooling",
]

class GemClassifier:
    def __init__(self, rubygems_service: RubyGemsService, llm_service: LLMService):
        self.rubygems = rubygems_service
        self.llm = llm_service

    def heuristic_classify(self, name: str, info: dict, category: Optional[str] = None) -> Tuple[GemClassification, GemSignals, list, List[str]]:
        lname = name.lower()
        def has(keys):
            return any(k in lname for k in keys)

        classification = GemClassification(primary="data_processing", confidence=0.5)
        signals = GemSignals()
        deps = [d["name"] for d in info.get("dependencies", {}).get("runtime", [])] if info else []

        # 1. runtime_spine (Boot + Wiring) — Rails core, frameworks, core Ruby exts
        if has(["rails", "engine", "railties", "active_support", "core_ext", "bundler"]):
            classification.primary = "runtime_spine"
            signals.rails = True
            classification.confidence += 0.3

        # 9. validation_types — Validation, type systems, schemas (before generic dry- check)
        elif has(["dry-validation", "dry-types", "dry-struct", "dry-schema",
                  "validates", "valid_attr", "attribute",
                  "json-schema", "json_schemer", "activemodel"]):
            classification.primary = "validation_types"
            classification.confidence += 0.3

        # runtime_spine fallback for other dry-* gems
        elif has(["dry-"]):
            classification.primary = "runtime_spine"
            signals.rails = True
            classification.confidence += 0.3

        # 2. cli_terminal_ui — CLI frameworks, TUI tools
        elif has(["thor", "gli", "gli", "tty-", "commander", "optimist", "clamp", "cli", "terminal", "curses", "ncurses"]):
            classification.primary = "cli_terminal_ui"
            classification.confidence += 0.3

        # 3. storage_persistence — ORMs, DB adapters, file stores
        elif has(["activerecord", "sequel", "rom", "mongoid", "ohm", "redis-store", "leveldb", "lmdb", "sqlite", "ar-"]):
            classification.primary = "storage_persistence"
            classification.confidence += 0.3

        # 4. async_networking_orchestration — HTTP, messaging, job queues, servers
        elif has(["sidekiq", "async", "falcon", "puma", "unicorn", "resque", "delayed_job",
                  "http", "faraday", "net-http", "excon", "typhoeus", "patron",
                  "grpc", "kafka", "bunny", "mqtt", "celluloid", "concurrent",
                  "eventmachine", "nio4r"]):
            classification.primary = "async_networking_orchestration"
            signals.external_io = True
            classification.confidence += 0.3

        # 5. ai_nlp — AI/ML, NLP, LLM, embeddings
        elif has(["openai", "ruby-openai", "anthropic", "llm", "gpt", "nlp", "langchain",
                  "transformers", "embedding", "vector", "tiktoken", "tokenizer",
                  "tensorflow", "torch", "onnx", "whisper"]):
            classification.primary = "ai_nlp"
            classification.confidence += 0.3

        # 6. data_processing — HTML/XML parsing, CSV, spreadsheets, PDF, scraping
        elif has(["nokogiri", "oga", "loofah", "sanitiz",
                  "roo", "spreadsheet", "caxlsx", "axlsx", "xlsx", "csv",
                  "prawn", "wicked_pdf", "pdfkit", "hexapdf",
                  "mechanize", "scraping", "scraper", "craw"]):
            classification.primary = "data_processing"
            classification.confidence += 0.3

        # 7. retrieval_similarity_fuzzy — Search, fuzzy matching, indexing
        elif has(["elasticsearch", "searchkick", "chewy", "meilisearch",
                  "fuzzy", "fuzz", "similar", "match",
                  "pg_search", "ransack", "sunspot", "thinking-sphinx"]):
            classification.primary = "retrieval_similarity_fuzzy"
            classification.confidence += 0.3

        # 8. algorithms_knowledge_structures — Data structures, algorithms, graph, tree
        elif has(["algorithm", "rbtree", "tree", "graph", "heap", "queue",
                  "set-theory", "bitset", "bloom", "trie", "hash_ring",
                  "priority-queue", "linked-list"]):
            classification.primary = "algorithms_knowledge_structures"
            classification.confidence += 0.3

        # 10. parsing_encoding — JSON, YAML, XML, MessagePack, serializers
        elif has(["json", "yajl", "oj", "msgpack", "yaml", "toml", "xml",
                  "serializ", "encode", "decode", "marshal", "protobuf"]):
            classification.primary = "parsing_encoding"
            classification.confidence += 0.3

        # 11. debugging_introspection — Debuggers, profilers, loggers, introspection
        elif has(["pry", "byebug", "debug", "debase", "ruby-debug",
                  "stackprof", "ruby-prof", "memory_profiler", "allocation_tracer",
                  "log", "logger", "sentry", "datadog", "newrelic", "honeybadger",
                  "bugsnag", "airbrake", "rollbar", "opentelemetry"]):
            classification.primary = "debugging_introspection"
            signals.external_io = True
            classification.confidence += 0.3

        # 12. mcp_tooling — MCP (Model Context Protocol) tools
        elif has(["mcp", "model-context-protocol"]):
            classification.primary = "mcp_tooling"
            classification.confidence += 0.3

        # Fallback: dependency-based heuristics
        else:
            for dep in deps:
                dl = dep.lower()
                if "rails" in dl or "activesupport" in dl:
                    classification.primary = "runtime_spine"
                    signals.rails = True
                    classification.confidence += 0.2
                    break
                if "sidekiq" in dl or "async" in dl or "falcon" in dl:
                    classification.primary = "async_networking_orchestration"
                    classification.confidence += 0.2
                    break
                if "sentry" in dl or "datadog" in dl or "newrelic" in dl:
                    classification.primary = "debugging_introspection"
                    classification.confidence += 0.2
                    break
                if "nokogiri" in dl or "csv" in dl:
                    classification.primary = "data_processing"
                    classification.confidence += 0.2
                    break

        if category in ["development", "test"]:
            classification.primary = "cli_terminal_ui"
            classification.confidence = max(classification.confidence, 0.7)

        if info and info.get("platform") not in (None, "ruby"):
            signals.native_ext = True

        # Sub-category detection from dependencies
        sub_cats = set()
        for dep in deps:
            dep_lower = dep.lower()
            if "rails" in dep_lower:
                sub_cats.add("rails")
            if "sidekiq" in dep_lower:
                sub_cats.add("sidekiq")
                sub_cats.add("background_jobs")
            if "active_job" in dep_lower or "activejob" in dep_lower:
                sub_cats.add("activejob")
            if "puma" in dep_lower or "unicorn" in dep_lower:
                sub_cats.add("server")
            if "redis" in dep_lower:
                sub_cats.add("redis")
            if "postgresql" in dep_lower or "pg" in dep_lower or "mysql" in dep_lower or "mysql2" in dep_lower or "mariadb" in dep_lower:
                sub_cats.add("database")
            if "elasticsearch" in dep_lower or "search" in dep_lower:
                sub_cats.add("search")
            if "jwt" in dep_lower or "oauth" in dep_lower:
                sub_cats.add("auth")
            if "graphql" in dep_lower or "grape" in dep_lower:
                sub_cats.add("api")
            if "json" in dep_lower or "xml" in dep_lower:
                sub_cats.add("serialization")
            if "csv" in dep_lower or "xlsx" in dep_lower or "excel" in dep_lower:
                sub_cats.add("spreadsheet")
            if "pdf" in dep_lower:
                sub_cats.add("pdf")
            if "aws" in dep_lower or "gcp" in dep_lower or "google" in dep_lower or "azure" in dep_lower:
                sub_cats.add("cloud")
            if "s3" in dep_lower:
                sub_cats.add("cloud")
            if "sentry" in dep_lower or "datadog" in dep_lower or "newrelic" in dep_lower or "honeybadger" in dep_lower:
                sub_cats.add("monitoring")
            if "delayed_job" in dep_lower or "resque" in dep_lower or "sidekiq" in dep_lower:
                sub_cats.add("background_jobs")
            if "kafka" in dep_lower or "bunny" in dep_lower or "mqtt" in dep_lower:
                sub_cats.add("messaging")

        return classification, signals, deps, list(sub_cats)

    def score_gem(self, primary_cat: str, deps: list) -> GemRisks:
        invasiveness_map = {
            "runtime_spine": 5,
            "async_networking_orchestration": 4,
            "storage_persistence": 4,
            "data_processing": 3,
            "ai_nlp": 3,
            "retrieval_similarity_fuzzy": 3,
            "algorithms_knowledge_structures": 2,
            "validation_types": 2,
            "parsing_encoding": 2,
            "cli_terminal_ui": 1,
            "debugging_introspection": 1,
            "mcp_tooling": 1,
        }
        inv = invasiveness_map.get(primary_cat, 3)

        coupling = min(4, max(1, len(deps)//3 + 1))

        leak_map = {
            "async_networking_orchestration": "high",
            "storage_persistence": "high",
            "runtime_spine": "medium",
            "ai_nlp": "medium",
            "data_processing": "medium",
        }
        leak = leak_map.get(primary_cat, "low")

        return GemRisks(invasiveness=inv, coupling=coupling, abstraction_leak=leak)

    def classify(self, name: str, category: Optional[str] = None, homepage: Optional[str] = None, source_code_uri: Optional[str] = None, context7_id: Optional[str] = None) -> GemEntry:
        info = self.rubygems.fetch_gem_info(name) or {}
        classification, signals, deps, sub_cats = self.heuristic_classify(name, info, category)
        
        # Add sub_categories to classification
        classification.sub_categories = sub_cats

        # Unconditionally call the LLM to generate an agent-optimized description
        prompt = self.llm.build_prompt(name, info, deps)
        llm_result = self.llm.call_llm(prompt)
        agent_desc = ""

        if llm_result:
            # Only override heuristic classification if LLM is confident and heuristics weren't
            if classification.confidence < 0.7 and llm_result.get("confidence", 0) > 0.6:
                classification.primary = llm_result["primary"]
                classification.confidence = llm_result["confidence"]
            agent_desc = llm_result.get("agent_description", "")

        risks = self.score_gem(classification.primary, deps)

        return GemEntry(
            name=name,
            classification=classification,
            role={
                "description": str(info.get("info", "")) if info else "",
                "agent_description": agent_desc,
                "attaches_to": classification.primary.split("_")[0]
            },
            risks=risks,
            signals=signals,
            dependencies=deps,
            description=info.get("info") if info else "",
            homepage=homepage,
            source_code_uri=source_code_uri,
            context7_id=context7_id
        )
