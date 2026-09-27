The `Context7Service` is a lightweight, stateless HTTP client that bridges RubyGemDB's classification pipeline with the [Context7 API](https://context7.com/api/v2/), a documentation-as-code service that provides structured usage cheatsheets and contextual lookups for open-source libraries. Unlike the other two external API integrations in the system — `RubyGemsService` (which caches responses in a JSON file) and `LLMService` (which caches in `llm_cache.json`) — the Context7Service deliberately maintains **zero local cache**, because its results are persisted directly by the caller into the SQLite `inventory` table as the `context7_id` column, or written as markdown cheatsheets to the filesystem. This service answers a fundamentally different question than the others: not "what is this gem?" (RubyGems) or "what category does it belong to?" (Classifier/LLM), but **"how do I use this gem in my project?"**

Sources: [context7.py](src/rubygemdb/services/context7.py#L1-L66), [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L14-L19)

## Service Architecture

The class is housed in a single 66-line file and exposes three public methods that follow a logical progression: **search** for a library, **verify** it exists, then **query** its documentation. The internal rate-limiter ensures a minimum 0.2-second gap between consecutive requests — a conservative value chosen to avoid triggering Context7's upstream throttling without needing a circuit breaker or exponential backoff.

```
┌─────────────────────────────────────────────────┐
│              Context7Service                    │
│  ┌───────────────────────────────────────────┐  │
│  │  _wait_for_rate_limit()                   │  │
│  │  └─ time.sleep(0.2 - elapsed)            │  │
│  │                                           │  │
│  │  search_libraries(gem_name)               │  │
│  │  └─ GET /api/v2/libs/search              │  │
│  │      ?libraryName={gem_name}              │  │
│  │      &query=ruby gem {gem_name} docs      │  │
│  │                                           │  │
│  │  verify_library(gem_name)                 │  │
│  │  └─ wraps search_libraries()              │  │
│  │     returns first result's id/libraryId   │  │
│  │                                           │  │
│  │  query_context(lib_id, query)             │  │
│  │  └─ GET /api/v2/context                  │  │
│  │      ?libraryId={lib_id}&query={query}    │  │
│  └───────────────────────────────────────────┘  │
│                                                  │
│  State: api_key, _last_request_time              │
│  Guard: returns []/None when api_key is falsy    │
└─────────────────────────────────────────────────┘
```

Sources: [context7.py](src/rubygemdb/services/context7.py#L6-L66)

### Method Breakdown

| Method | Endpoint | Timeout | Return on Failure | Primary Consumer |
|---|---|---|---|---|
| `search_libraries(name)` | `/api/v2/libs/search` | 5s | `[]` (empty list) | CLI verification, TUI library lookup |
| `verify_library(name)` | (wraps `search_libraries`) | 5s | `None` | SQLiteStorage `load_inventory()` |
| `query_context(lib_id, query)` | `/api/v2/context` | **30s** | `None` | TUI cheatsheet generation |

The asymmetry in timeouts is intentional: library search needs to be fast because it sits in the interactive CLI loop where a human is waiting for suggestions; context query can take longer because it fetches potentially large documentation bodies that are written to disk. A failed context query does not block the pipeline — the method catches all exceptions silently and returns `None`, mirroring the graceful-degradation pattern used throughout the application.

Sources: [context7.py](src/rubygemdb/services/context7.py#L18-L38), [context7.py](src/rubygemdb/services/context7.py#L40-L45), [context7.py](src/rubygemdb/services/context7.py#L47-L65)

## Dual Integration Pathways

One of the most interesting architectural decisions in RubyGemDB is that Context7 is accessed through **two completely separate protocols** depending on the context: a direct REST API client (this service) for the CLI/TUI pipeline, and a Model Context Protocol (MCP) server for the AI Agent. These pathways serve different purposes and operate independently.

### Pathway 1 — REST API (Pipeline Integration)

The `Context7Service` is instantiated in three places across the codebase, and in each case it is injected into `SQLiteStorage` as a dependency:

```python
# CLI entry point (cli.py)
c7_service = Context7Service()
storage = SQLiteStorage(rubygems_service=rg_service, context7_service=c7_service)

# TUI entry point (ui/tui.py)
self.c7_service = Context7Service()
self.storage = SQLiteStorage(rubygems_service=self.rg_service, context7_service=self.c7_service)

# SQLiteStorage fallback (if no service passed)
self.context7 = context7_service or Context7Service()
```

This dependency-injection pattern enables testing: the TUI and CLI share a single service instance (and therefore a single rate-limit clock), while `SQLiteStorage` can be instantiated without any external API dependency when running in offline mode.

Sources: [cli.py](src/rubygemdb/cli.py#L57-L59), [tui.py](src/rubygemdb/ui/tui.py#L1023-L1025), [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L15-L18)

### Pathway 2 — MCP Protocol (Agent Integration)

In `agent.py`, a separate `@upstash/context7-mcp` server is spawned as a subprocess via `npx` and connected through the Model Context Protocol:

```python
c7_mcp = MCPClient(
    StdioServerParameters(command="npx", args=["-y", "@upstash/context7-mcp"]),
    structured_output=False,
)
```

This MCP client exposes tools like `resolve-library-id` and `query-docs` to the AI agent. Critically, the agent **wraps every MCP tool's `forward` method** at initialization time to intercept results and index them into the txtai vector database:

```python
for tool_obj in c7_tools:
    original_forward = tool_obj.forward
    def wrap_forward(orig=original_forward, tool_name=tool_obj.name):
        def forward_interceptor(*args, **kwargs):
            result = orig(*args, **kwargs)
            if isinstance(result, str) and result.strip():
                self.index_memory(result, doc_type="context7",
                                  metadata={"source_tool": tool_name})
            return result
        return forward_interceptor
    tool_obj.forward = wrap_forward()
```

This auto-indexing means that every documentation snippet fetched via the agent becomes searchable in future chat sessions without requiring a manual re-index step — a form of **persistent retrieval-augmented generation (RAG)** that accumulates knowledge across sessions.

Sources: [agent.py](src/rubygemdb/agent.py#L227-L256)

## Data Flow: From Gem Name to Documentation

The lifecycle of a Context7 interaction typically follows one of three trajectories depending on the entry point. The most complete flow occurs during the CLI's Phase 1 metadata verification:

```
CLI Phase 1 Flow

  ┌──────────┐     ┌──────────────────┐     ┌─────────────────┐
  │ CSV Row  │────>│  SQLiteStorage   │────>│ RubyGemsService │
  │ gem name │     │  load_inventory  │     │ fetch_gem_info   │
  └──────────┘     └──────────────────┘     └─────────────────┘
                              │
                              ▼
                   ┌──────────────────┐
                   │  Check DB for    │
                   │  existing record │
                   │  (verified=1?)   │
                   └──────────────────┘
                     │          │
                     │ (miss)   │ (hit → skip)
                     ▼
                   ┌──────────────────┐
                   │ Context7Service  │
                   │ verify_library() │
                   └──────────────────┘
                     │
                     ▼
                   ┌──────────────────────┐
                   │  Interactive Prompt  │
                   │  (CLI only)          │
                   │  "Accept context7_id │
                   │   'rails/rails'?"    │
                   └──────────────────────┘
                     │
                     ├── yes → store in SQLite inventory.context7_id
                     ├── no  → show alternatives, let user pick
                     └── skip → leave blank, continue
```

The `verify_library` method called during SQLite's `load_inventory()` is deliberately simpler than the full interactive flow: it takes the first search result's `id` or `libraryId` and returns it without user confirmation. The full interactive selection with multiple choices, re-search, and skip is implemented only in `cli.py` lines 133-175, which calls `search_libraries()` directly rather than `verify_library()`.

Sources: [cli.py](src/rubygemdb/cli.py#L132-L175), [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L83-L84), [context7.py](src/rubygemdb/services/context7.py#L40-L45)

## TUI Interactive Usage

The TUI provides two Context7-powered operations accessible from the gem details panel and the keyboard shortcuts:

**Context7 ID Lookup** (`lookup_c7_id`): Runs as a threaded worker that calls `self.c7_service.search_libraries(gem_name)`, then pushes a `C7SelectionScreen` modal — a custom Textual screen that lists all matching libraries with radio-button selection and a manual-entry input field. When the user confirms a selection, the ID is persisted to SQLite via `storage.update_gem_metadata()` and the local `all_gems` list is updated in-memory to keep the UI responsive without a full reload.

**Cheatsheet Fetch** (`fetch_cheatsheet`): Uses a two-phase ID resolution — first checks the local `all_gems` list for a stored `context7_id`, falls back to `self.c7_service.verify_library()` if none is found. Once resolved, it calls `self.c7_service.query_context(lib_id, prompt)` with a prompt specifically scoped to "genai application, nlp text processing, or as a systems tool." The returned text is formatted into a markdown cheatsheet at `output/cheatsheets/{name}_cheatsheet.md`. This targeted prompt reflects the architectural bias of the entire system — RubyGemDB was designed for AI/LLM pipeline developers who need to understand how a gem fits into a data-processing or agents context.

Sources: [tui.py](src/rubygemdb/ui/tui.py#L1625-L1651), [tui.py](src/rubygemdb/ui/tui.py#L1652-L1730)

## Graceful Degradation and Configuration

The service is fully optional. When `settings.context7_api_key` is `None` (or unset), every public method becomes a no-op:

```python
def search_libraries(self, gem_name: str) -> list:
    if not self.api_key:
        return []        # ← silent degradation, not an exception
```

This guard makes the entire Context7 integration invisible to users who have not configured the API key — the pipeline proceeds without Context7 enrichment, the TUI shows "Missing" in the Context7 ID column, and the cheatsheet buttons show error notifications. The setting is loaded from the `CONTEXT7_API_KEY` environment variable via `pydantic-settings`:

```ini
# .env
CONTEXT7_API_KEY=your_context7_key_here
```

There is an architectural curiosity around the rate-limit configuration: `Settings` defines a `rate_limit_delay` field (line 18 of `config.py`) that appears intended for cross-service rate limiting, but `Context7Service` ignores it in favour of a hardcoded `self.rate_limit_delay = 0.2` (line 10). The settings field is dead configuration — it exists only in the model and is never consumed by any service.

Sources: [context7.py](src/rubygemdb/services/context7.py#L19), [config.py](src/rubygemdb/core/config.py#L8), [tui.py](src/rubygemdb/ui/tui.py#L1654-L1656)

## The `context7_id` as a Cross-Cutting Primitive

The `context7_id` field — a string like `"/rails/rails"` or `"/marcoroth/lipgloss-ruby"` — appears in five separate data structures across the codebase, making it one of the most widely propagated identifiers in the system:

| Location | Type | Purpose |
|---|---|---|
| `GemInventoryItem.context7_id` | `Optional[str]` | CSV/DB row representation |
| `GemEntry.context7_id` | `Optional[str]` | Full classified gem record |
| SQLite `inventory.context7_id` column | `TEXT` | Primary persistence column |
| JSON export | string field | Export fidelity |
| txtai index metadata | `context7_id` key | Semantic search cross-reference |

This propagation means that once a Context7 ID is resolved for a gem, it follows that gem through every stage of the pipeline — from inventory loading through classification, storage, export, and into the vector index. The AI agent's `search_memory_tool` returns `context7_id` alongside each result, enabling downstream consumers to fetch full documentation for any semantically matched gem.

Sources: [gem.py](src/rubygemdb/models/gem.py#L31), [gem.py](src/rubygemdb/models/gem.py#L40), [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L34), [agent.py](src/rubygemdb/agent.py#L519)

## Testing Considerations

The `Context7Service` is not directly tested in the test suite (`tests/` contains only `test_classifier.py` and `test_export.py`). The export tests do exercise `context7_id` propagation through the TUI export methods — for example, `test_export_json_format` creates a `GemEntry` with `c7_id="/rails/rails"` and verifies it survives the JSON serialisation round-trip. Any direct testing of the service would require either mocking the `requests.get` call or providing a live Context7 API key, which explains the absence: the service is a thin HTTP wrapper, and its behaviour is fully determined by the API response shape. The verification logic (`verify_library` returning the first result's `id` or `libraryId`) is simple enough that testing the adapter itself provides more confidence than testing the service in isolation.

Sources: [test_export.py](tests/test_export.py#L64-L76)

## Next Steps

- Understand how the verified metadata flows into the [Heuristic Rule-Based Classification](13-heuristic-rule-based-classification) engine
- See how `context7_id` becomes a searchable field in the [txtai-Based Vector Embeddings & Indexing](20-txtai-based-vector-embeddings-and-indexing) system
- Explore how the MCP pathway complements the REST pathway in the [MCP Tool Integration for Context & Task Management](22-mcp-tool-integration-for-context-and-task-management) documentation