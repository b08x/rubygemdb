The storage layer sits at the boundary between in-memory processing and durable persistence in rubygemdb. It is governed by an abstract base class (`StorageBase`) that defines a minimal contract for loading raw inventory data and persisting classified gem results, while each concrete implementation brings its own strategy for schema design, metadata verification, and operational tooling. The architecture follows a classic **Strategy Pattern**: the classifier pipeline, CLI, and TUI all depend on the interface rather than any specific backend, enabling the JSON fallback for lightweight or development use and the SQLite backend for production-grade metadata management.

## Abstract Contract: `StorageBase`

The abstract class resides in `storage/base.py` and declares three methods that every backend must implement. These form the minimal surface area that the rest of the application requires to function:

| Method | Purpose | Input | Output |
|--------|---------|-------|--------|
| `load_inventory(path: str) -> List[GemInventoryItem]` | Reads a CSV file (provided by the user) and returns normalized inventory items | File path to CSV | List of `GemInventoryItem` Pydantic models |
| `save_classified_gems(gems: List[GemEntry])` | Persists fully classified gem data for later retrieval | List of `GemEntry` models | `None` (side-effect: writes to DB or file) |
| `load_classified_gems() -> List[GemEntry]` | Retrieves previously classified gems from storage | None | List of `GemEntry` models |

Sources: [base.py](src/rubygemdb/storage/base.py#L1-L17)

Both concrete implementations accept `GemInventoryItem` (a lightweight model with optional metadata fields like `version`, `homepage`, `context7_id`) and `GemEntry` (the full classification result including `GemClassification`, `GemRisks`, `GemSignals`, dependencies, and cross-referenced metadata). The models are defined in `models/gem.py` and act as the data transfer objects that flow across the storage boundary.

Sources: [gem.py](src/rubygemdb/models/gem.py#L1-L41)

## SQLiteStorage — The Production Implementation

`SQLiteStorage`, defined in `storage/sqlite_storage.py`, is the primary backend used in both the CLI pipeline and the TUI. It is substantially more complex than the abstract contract demands, because it embeds the **metadata verification workflow** directly into the load path.

### Schema Design

The implementation manages two tables within a single SQLite database file located at `settings.sqlite_db_file` (defaults to `data/rubygemdb.sqlite`):

```
┌──────────────────────────────┐
│         inventory            │
├──────────────────────────────┤
│ name TEXT PRIMARY KEY        │
│ version TEXT                 │
│ category TEXT                │
│ description TEXT             │
│ homepage TEXT                │
│ source_code_uri TEXT         │
│ context7_id TEXT             │
│ verified INTEGER DEFAULT 0   │
└──────────────────────────────┘
         │
         │ 1 ──── 0..1 (FK: name)
         ▼
┌──────────────────────────────┐
│      classified_gems         │
├──────────────────────────────┤
│ name TEXT PRIMARY KEY        │
│ classification TEXT (JSON)   │
│ role TEXT (JSON)             │
│ capabilities TEXT (JSON)     │
│ risks TEXT (JSON)            │
│ signals TEXT (JSON)          │
│ dependencies TEXT (JSON)     │
│ description TEXT             │
│ FOREIGN KEY → inventory(name)│
└──────────────────────────────┘
```

Sources: [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L21-L53)

The `inventory` table holds raw metadata (homepage, source code URI, Context7 library ID) along with a `verified` boolean flag that indicates whether the record has been cross-checked against live APIs. The `classified_gems` table stores the structured classification output as serialized JSON blobs — each complex Pydantic field (`GemClassification`, `GemRisks`, `GemSignals`, etc.) is dumped to JSON before insertion and parsed back on retrieval.

### Constructor Dependency Injection

The constructor accepts two optional service objects — `RubyGemsService` and `Context7Service` — and falls back to instantiating defaults if none are provided:

```python
def __init__(self, rubygems_service: Optional[RubyGemsService] = None, 
             context7_service: Optional[Context7Service] = None):
    self.db_path = settings.sqlite_db_file
    self.rubygems = rubygems_service or RubyGemsService()
    self.context7 = context7_service or Context7Service()
    self._init_db()
```

This design enables the CLI and TUI to inject shared service instances (for connection reuse and rate-limit coordination) while allowing standalone instantiation for testing or scripting. The constructor always calls `_init_db()`, which creates both tables with `CREATE TABLE IF NOT EXISTS`, guaranteeing the schema exists without external migration scripts.

Sources: [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L14-L53)

### Metadata Verification in `load_inventory`

The `load_inventory` method does far more than its abstract signature suggests. It reads a CSV file row by row and, for each gem name, checks the database for an existing verified record. If a verified record with a non-null `source_code_uri` exists, it skips the API calls and reuses the cached metadata. Otherwise, it performs **two live lookups**:

1. **RubyGems API** — fetches `homepage_uri` and `source_code_uri` via `RubyGemsService.fetch_gem_info()`
2. **Context7 API** — verifies or discovers the Context7 library ID via `Context7Service.verify_library()`

The results are written into the `inventory` table with `verified=1` using an `INSERT ... ON CONFLICT DO UPDATE` upsert. This two-phase strategy — fast-path for cached entries, slow-path with live verification for new or unverified rows — is the core optimization that prevents redundant API calls across multiple runs.

Sources: [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L55-L110)

### Rich CRUD Surface Beyond the Interface

`SQLiteStorage` exposes several methods that have no counterpart in `StorageBase`. These exist because the TUI and CLI need operational capabilities that the abstract contract does not mandate:

| Method | Purpose | Used By |
|--------|---------|---------|
| `update_gem_metadata(name, context7_id, source_code_uri)` | Partial field update on inventory | CLI verification phase |
| `update_gem_verification(name, homepage, source_code_uri, description, context7_id)` | Full verification write-back | CLI phase 1 |
| `delete_gem(name)` | Removes gem from both tables | CLI cleanup of hallucinated gems |
| `update_gem_classification(name, primary_category)` | Manual classification override | TUI user correction (sets confidence to 1.0) |
| `add_gem_to_inventory(name)` | Inserts a gem name for later classification | TUI addition workflow |
| `get_all_inventory_gems() -> List[dict]` | Returns non-classified inventory rows | CLI fallback when no CSV provided |

Sources: [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L140-L238)

These extra methods reveal that `SQLiteStorage` has evolved into the de facto **inventory management system** — not merely a persistence backend but an active component that coordinates metadata enrichment, verification state tracking, and user-driven corrections. The abstract interface only captures the read/write contract for the ML pipeline; the concrete class carries the operational burden.

### Deserialization in `load_classified_gems`

The reverse path reconstructs `GemEntry` objects by joining `classified_gems` with `inventory` to recover metadata fields (`homepage`, `source_code_uri`, `context7_id`). Each JSON column is parsed back into its corresponding Pydantic model:

```python
gems.append(GemEntry(
    name=row["name"],
    classification=GemClassification(**json.loads(row["classification"])),
    role=json.loads(row["role"]),
    capabilities=json.loads(row["capabilities"]),
    risks=GemRisks(**json.loads(row["risks"])),
    signals=GemSignals(**json.loads(row["signals"])),
    dependencies=json.loads(row["dependencies"]),
    description=row["description"],
    homepage=row["homepage"],
    source_code_uri=row["source_code_uri"],
    context7_id=row["context7_id"]
))
```

This round-trip relies on the Pydantic models maintaining backward-compatible field definitions — any schema change to `GemClassification`, `GemRisks`, or `GemSignals` would break deserialization of previously persisted rows.

Sources: [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L188-L214)

## JSONStorage — Lightweight Fallback

`JSONStorage` in `storage/json_storage.py` implements the abstract contract with minimal overhead. It reads CSV inventory like the SQLite variant but performs **no metadata verification** — it directly maps CSV columns to `GemInventoryItem` fields without any API calls. For saving and loading classified gems, it serializes the entire gem list to a single JSON file at `settings.classified_gems_file`.

```python
def save_classified_gems(self, gems: List[GemEntry]):
    data = [gem.model_dump() for gem in gems]
    with open(settings.classified_gems_file, "w") as f:
        json.dump(data, f, indent=2)
```

Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L1-L37)

The critical distinction between the two implementations:

| Aspect | SQLiteStorage | JSONStorage |
|--------|---------------|-------------|
| **Metadata verification** | Built-in with RubyGems + Context7 API calls | None |
| **State tracking** | `verified` flag, upsert logic | No state — full rewrite |
| **Concurrent access** | SQLite row-level locking | File-level write, no locking |
| **Query capability** | SQL JOINs, partial updates | Full load → modify → dump |
| **Extra methods** | 6 operational methods beyond interface | None beyond interface |
| **Use case** | Production CLI/TUI | Development, testing, simple fallback |

JSONStorage is not currently wired into any application entry point — both `cli.py` and `tui.py` import `SQLiteStorage` directly. The JSON backend exists as an architectural option for scenarios where SQLite is unavailable or where a human-readable file format aids debugging.

## Usage Flow in the Application

The storage layer participates in three distinct workflows:

### CLI Batch Pipeline

```
CSV Inventory ──► SQLiteStorage.load_inventory()
                         │
                    [Metadata verification via RubyGems + Context7]
                         │
                         ▼
              List[GemInventoryItem] ──► GemClassifier.classify()
                         │
                         ▼
              List[GemEntry] ──► SQLiteStorage.save_classified_gems()
                         │
                         ▼
              YAML reports per category
```

The `load_inventory` call in the CLI uses the CSV file path from the user argument, and the returned items are transformed into dicts for processing. After classification, `save_classified_gems` persists the results back to SQLite for later use by the TUI or txtai indexing. Sources: [cli.py](src/rubygemdb/cli.py#L55-L67), [cli.py](src/rubygemdb/cli.py#L231-L232)

### TUI Interactive Mode

```
┌────────────────────────────────────────────────┐
│  GemApp.__init__()                              │
│    SQLiteStorage(rubygems_service,              │
│                  context7_service)              │
│                                                 │
│  On inventory load:                             │
│    storage.load_inventory(path)                 │
│       ── or ──                                  │
│    storage.get_all_inventory_gems()             │
│                                                 │
│  On classification correction:                  │
│    storage.update_gem_classification()          │
│                                                 │
│  On gem removal:                                │
│    storage.delete_gem()                         │
│                                                 │
│  On new gem addition:                           │
│    storage.add_gem_to_inventory()               │
└────────────────────────────────────────────────┘
```

The TUI (`ui/tui.py`) constructs `SQLiteStorage` with the same service instances used by the classifier and chat components, ensuring consistent rate limiting and caching across the application. Sources: [tui.py](src/rubygemdb/ui/tui.py#L1025)

### txtai Index Building

The agent module (`agent.py`) bypasses the storage abstraction entirely, connecting directly to the SQLite database file using `sqlite3.connect`. It reads from the `inventory` table and joins with the `gem_cache.json` file to build a txtai embedding index. This direct access is justified because the indexing operation needs raw column data in a batch stream, not the classified `GemEntry` objects. Sources: [agent.py](src/rubygemdb/agent.py#L129-L136)

## Design Trade-offs and Architectural Notes

The abstraction boundary between `StorageBase` and its implementations reveals a tension between **contract minimalism** and **operational reality**. The three-method interface cleanly separates the concerns of the classification pipeline (which only needs to load inventory and save/load results), but the concrete SQLite implementation has grown six additional public methods to support interactive workflows. This suggests one of two future refactoring paths: either the interface should be expanded to include inventory CRUD operations (at the cost of making JSONStorage more complex), or the operational methods should be extracted into a separate `InventoryManager` interface that only SQLiteStorage implements.

The JSON serialization strategy — storing Pydantic models as JSON blobs in a relational column — is a pragmatic trade-off. It avoids complex schema migrations when classification fields evolve, but sacrifices the ability to query on nested fields (e.g., "find all gems with `risks.invasiveness > 3`") without loading and parsing every row. For a project where classification is the primary output and ad-hoc queries are rare, this is a reasonable choice.

## Next Steps in the Storage Layer

The two concrete implementations that follow this interface are explored in detail on their respective pages:

- **[SQLite Storage with Metadata Verification](11-sqlite-storage-with-metadata-verification)** — deep dive into the verification workflow, upsert patterns, and concurrent access considerations
- **[JSON File Storage Fallback](12-json-file-storage-fallback)** — usage patterns for the lightweight backend and when to choose it over SQLite