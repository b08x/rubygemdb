The `JSONStorage` class in `storage/json_storage.py` provides a zero-dependency persistence backend that implements the `StorageBase` abstract contract without requiring SQLite, external API services, or any database schema. It represents the lightweight alternative to `SQLiteStorage` — a deliberately simple fallback that trades enrichment capabilities for architectural minimalism. Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L1-L37)

## Architectural Role: Why a Fallback Exists

The `StorageBase` interface defines three methods that every persistence backend must satisfy: `load_inventory`, `save_classified_gems`, and `load_classified_gems`. `SQLiteStorage` implements these with deep integration into external APIs (RubyGems.org, Context7) and a relational schema that tracks verification state across two tables. `JSONStorage` implements the same three methods through pure file I/O — reading CSV for inventory, writing JSON for classified results, and performing zero network calls.

This dual-implementation pattern serves two architectural goals. First, it decouples the classification pipeline from the storage engine: the `GemClassifier` never touches storage directly, meaning you can classify gems to JSON files without setting up a database. Second, it provides a debugging and development workflow where the entire classified output lives in a single human-readable file at `data/classified_gems.json`, inspectable with any text editor or `jq` query. Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L26-L36), [config.py](src/rubygemdb/core/config.py#L29)

## Implementation Walkthrough

The class is contained entirely within 37 lines and implements the three abstract methods with straightforward logic:

### `load_inventory(path: str) -> List[GemInventoryItem]`

```python
def load_inventory(self, path: str) -> List[GemInventoryItem]:
    items = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("gem") or row.get("name")
            if name:
                items.append(GemInventoryItem(
                    name=name,
                    version=row.get("version"),
                    category=row.get("category"),
                    description=row.get("description"),
                    homepage=row.get("homepage"),
                    context7_id=row.get("context7_id")
                ))
    return items
```

This method reads a CSV file using `csv.DictReader` and performs a straightforward column mapping. It accepts two possible column headers for the gem name (`"gem"` or `"name"`), providing tolerance for variations in inventory file format. Critically, **no metadata verification occurs** — unlike `SQLiteStorage.load_inventory()` which calls `RubyGemsService.fetch_gem_info()` and `Context7Service.verify_library()` for each row, this method passes through whatever data the CSV provides, including null values. If a row lacks a recognizable name column, it is silently skipped. Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L9-L24)

### `save_classified_gems(gems: List[GemEntry])`

```python
def save_classified_gems(self, gems: List[GemEntry]):
    data = [gem.model_dump() for gem in gems]
    with open(settings.classified_gems_file, "w") as f:
        json.dump(data, f, indent=2)
```

The entire classified gem list is serialised as a JSON array using Pydantic's `model_dump()` method (the modern replacement for `dict()` in Pydantic v2). Each `GemEntry` becomes a flat JSON object with nested structures for `classification`, `risks`, and `signals` rendered as embedded objects. The output is written with 2-space indentation to `settings.classified_gems_file`, which defaults to `data/classified_gems.json` relative to the project root. This is a **full-file replacement** strategy — every call overwrites the entire file, making it unsuitable for concurrent write access or partial updates. Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L26-L29), [config.py](src/rubygemdb/core/config.py#L29)

### `load_classified_gems() -> List[GemEntry]`

```python
def load_classified_gems(self) -> List[GemEntry]:
    if not settings.classified_gems_file.exists():
        return []
    with open(settings.classified_gems_file, "r") as f:
        data = json.load(f)
        return [GemEntry(**item) for item in data]
```

Deserialization checks for file existence first — returning an empty list if no classified gems have been saved yet — then reconstructs each `GemEntry` by unpacking the JSON dictionaries directly into the Pydantic constructor via `**item`. This relies on `GemEntry.__init__` accepting nested dicts for its complex fields (`classification`, `risks`, `signals`), which Pydantic handles automatically through field validation. If the JSON file contains schema-incompatible data, the deserialization will raise a `ValidationError` at construction time. Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L31-L36)

## Data Flow Comparison: JSONStorage vs SQLiteStorage

The two implementations diverge most sharply in the `load_inventory` path — the point where `SQLiteStorage` enriches data and `JSONStorage` passes it through:

| Stage | SQLiteStorage | JSONStorage |
|-------|---------------|-------------|
| **CSV parsing** | `csv.DictReader` | `csv.DictReader` |
| **Gem name extraction** | Same `"gem" or "name"` logic | Same fallback logic |
| **Metadata verification** | RubyGems API + Context7 API per gem | **None — raw CSV values used** |
| **Cache/reuse** | `verified` flag skips API hits | N/A — no state |
| **Inventory persistence** | Written to `inventory` table with upsert | **Not persisted — kept in memory only** |
| **Return value** | `GemInventoryItem` with enriched fields | `GemInventoryItem` with CSV-original fields |

Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L9-L24), [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L55-L110)

The `save_classified_gems` and `load_classified_gems` methods are functionally equivalent to the SQLite serialisation path (both use `json.dumps`/`json.loads` internally on the same Pydantic models), but the storage medium differs — a flat JSON file versus a relational row:

| Aspect | JSONStorage | SQLiteStorage |
|--------|-------------|---------------|
| **Write strategy** | Full file overwrite | Row-level upsert per gem |
| **Serialisation format** | Pretty-printed JSON array | JSON blobs in relational columns |
| **Metadata round-trip** | Only fields in `GemEntry` are stored | JOIN with `inventory` recovers metadata |
| **Concurrency** | Unsafe — last writer wins | Row-level locking via SQLite |
| **Scalability limit** | Entire dataset must fit in memory | Streamed via cursor |
| **Human readability** | Full file viewable in editor | Requires SQLite client or query |

Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L26-L36), [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L112-L214)

## Integration Status: Available but Unwired

`JSONStorage` is **defined in the codebase but not imported by any application entry point**. Both `cli.py` and `ui/tui.py` import `SQLiteStorage` directly:

```
cli.py:16   from rubygemdb.storage.sqlite_storage import SQLiteStorage
tui.py:21   from rubygemdb.storage.sqlite_storage import SQLiteStorage
```

Sources: [cli.py](src/rubygemdb/cli.py#L16), [tui.py](src/rubygemdb/ui/tui.py#L21)

This means the JSON fallback is an **architectural affordance** — it exists as a drop-in replacement that any entry point could use by simply changing the import and, in the case of `SQLiteStorage`, avoiding the constructor dependency on `RubyGemsService` and `Context7Service`:

```python
# Current (SQLite):
from rubygemdb.storage.sqlite_storage import SQLiteStorage
storage = SQLiteStorage(rubygems_service=rg_service, context7_service=c7_service)

# Alternative (JSON):
from rubygemdb.storage.json_storage import JSONStorage
storage = JSONStorage()  # No service dependencies needed
```

The lack of a factory function or configuration-based backend selection means the choice is currently compile-time rather than runtime-configurable. The [Storage Abstraction Interface](10-storage-abstraction-interface) documentation discusses this design tension and potential refactoring paths.

## When to Use JSONStorage

The fallback is appropriate in four scenarios:

**Debugging and inspection** — When you need to examine the full classified output as a single readable file. Each run overwrites `classified_gems.json`, giving you a snapshot of exactly what was produced. Compare this to `SQLiteStorage` where classified data is spread across two tables with JSON blobs in columns.

**Rapid prototyping** — When testing the classification pipeline without configuring API keys or database setup. `JSONStorage` requires only a CSV inventory file and write access to the data directory. No `.env` file, no SQLite, no network dependencies.

**Single-run export** — When the goal is a one-shot classification run whose output will be consumed by another tool, not queried interactively. Writing to JSON avoids the overhead of schema creation and database connection management.

**CI/CD and testing** — When running automated tests that should not depend on external API availability or database state. A `JSONStorage` backend in tests eliminates the need for SQLite fixtures or mock databases.

In contrast, `SQLiteStorage` should remain the default for all interactive and production workflows because it provides metadata verification, verification state tracking, partial updates, and the CRUD surface that the TUI relies on (especially `update_gem_classification`, `delete_gem`, and `add_gem_to_inventory` which have no JSON counterpart). Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L1-L37)

## Output Format Specification

When `JSONStorage.save_classified_gems()` writes to `classified_gems.json`, the file conforms to this structure:

```json
[
  {
    "name": "rails",
    "classification": {
      "primary": "runtime_spine",
      "secondary": "storage_persistence",
      "sub_categories": ["web_framework", "orm"],
      "confidence": 0.95
    },
    "role": {
      "primary": "web application framework",
      "ecosystem": "ruby_on_rails"
    },
    "capabilities": [
      "MVC architecture",
      "database migrations",
      "routing"
    ],
    "risks": {
      "invasiveness": 3,
      "coupling": 4,
      "abstraction_leak": "medium"
    },
    "signals": {
      "rails": true,
      "external_io": true,
      "native_ext": false
    },
    "dependencies": ["actionmailer", "activerecord", "activesupport"],
    "description": "Ruby on Rails is a full-stack web framework...",
    "homepage": "https://rubyonrails.org",
    "source_code_uri": "https://github.com/rails/rails",
    "context7_id": "rails/rails"
  }
]
```

This is the same schema that `SQLiteStorage` uses internally (serialised as individual JSON columns), so files written by `JSONStorage` are structurally compatible with the `GemEntry` deserialisation path used by `SQLiteStorage.load_classified_gems()`. The only difference is the storage container: a standalone file versus a database row. Sources: [gem.py](src/rubygemdb/models/gem.py#L20-L31)

## Limitations and Design Gaps

The simplification of `JSONStorage` introduces several limitations that are by design but worth enumerating:

**No incremental writes** — Every call to `save_classified_gems` reads the entire gem list into memory, serialises all entries, and writes them to disk. For inventories exceeding a few thousand gems, this becomes both memory-intensive and I/O-heavy compared to SQLite's row-level upsert.

**No verification state** — The `verified` flag that `SQLiteStorage` uses to avoid redundant API calls has no equivalent here. Each run re-parses the CSV from scratch without enrichment. This is acceptable for single-pass workflows but wasteful if the same inventory is processed repeatedly.

**No query capability** — Loading classified gems always returns the entire dataset. There is no `get_all_inventory_gems()`, no `delete_gem()`, no `update_gem_classification()`. The TUI's interactive correction workflow — which depends on targeted updates — cannot be supported without reading and rewriting the full JSON file for every mutation.

**No transactional safety** — If the process crashes during `save_classified_gems`, the output file may contain a partial write, corrupting the previously persisted state. `SQLiteStorage` benefits from SQLite's atomic commit protocol.

These gaps are not bugs; they reflect the design principle that `JSONStorage` is a fallback for stateless, ephemeral workflows. The [Storage Abstraction Interface](10-storage-abstraction-interface) page explores whether the abstract contract should evolve to accommodate both backends more evenly.

## Summary

`JSONStorage` is a 37-line implementation of the `StorageBase` contract that reads CSV inventory directly and persists classified gems to a single JSON file. It performs no metadata verification, maintains no relational state, and exposes no methods beyond the three required by the interface. It is currently unwired from all application entry points but exists as an architectural option for debugging, prototyping, single-run exports, and testing scenarios where SQLite and external API dependencies are undesirable. For production use — especially interactive workflows requiring metadata enrichment, partial updates, or concurrent access — the [SQLite Storage with Metadata Verification](11-sqlite-storage-with-metadata-verification) backend remains the appropriate choice.