The RubyGemDB TUI is a full-screen interactive application built with the [Textual](https://textual.textualize.io/) framework (v8.2.3) that provides a multi-tab interface for exploring, classifying, chatting about, and exporting Ruby gems. It serves as the primary interactive user interface, complementing the CLI batch pipeline and the agent-powered semantic chat. The TUI integrates every major service in the system — storage, classification, RubyGems API, Context7, LLM, and the txtai agent — into a cohesive keyboard-driven experience.

**Sources**: [pyproject.toml](../../pyproject.toml#L3-L8), [tui.py](src/rubygemdb/ui/tui.py#L1-L53)

---

## Application Architecture & Entry Point

The TUI is launched via the `rubygemdb-tui` console script defined in `pyproject.toml`, which maps to `rubygemdb.ui.tui:run_tui`. The `run_tui()` function accepts an optional CSV path argument for loading initial inventory data into the SQLite database before launching the interface.

```python
def run_tui():
    parser = argparse.ArgumentParser(description="RubyGemDB Explorer TUI")
    parser.add_argument("csv", nargs="?", help="Optional path to gems inventory CSV")
    args = parser.parse_args()
    app = GemApp(inventory_path=args.csv)
    app.run()
```

**Sources**: [pyproject.toml](../../pyproject.toml#L30), [tui.py](src/rubygemdb/ui/tui.py#L1804-L1815)

The `GemApp` class (extending `textual.app.App`) is the root application container. On instantiation, it initializes all service dependencies:

| Service | Object | Role |
|---|---|---|
| RubyGemsService | `self.rg_service` | Fetch gem metadata from rubygems.org API |
| LLMService | `self.llm_service` | Mistral LLM for classification augmentation |
| Context7Service | `self.c7_service` | Documentation search and library ID resolution |
| GemClassifier | `self.classifier` | Heuristic + LLM classification pipeline |
| SQLiteStorage | `self.storage` | Persistence layer (inventory + classified gems) |
| TxtaiAgent | `self._txtai_agent` | Vector embeddings + smolagents agent (lazy-loaded) |

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L1018-L1035)

---

## Screen Layout & Navigation

### Composition Strategy

The UI is assembled in `compose()` using Textual's declarative container/widget system:

```
┌─────────────────────────────────────────────────────────┐
│ Header (show_clock=True)                                │
├──────────┬──────────────────────────────────────────────┤
│ Sidebar  │  Tabbed Content (Explorer | Chat | Export    │
│ (30w)    │               | Debug)                        │
│          │                                                │
│ Filter   │  [ DataTable | AgentChatTab | ExportTab      │
│ Stats    │    | RichLog ]                                │
├──────────┴──────────────────────────────────────────────┤
│ Footer                                                  │
│ Details Sidebar (50w, initially hidden)                  │
└─────────────────────────────────────────────────────────┘
```

**Layout hierarchy**: `Horizontal(sidebar:Vertical + main-area:Vertical)` contains the primary structure. Inside `main-area`, a `TabbedContent` widget hosts four named panes managed via `TabConstants`:

| Tab ID | Widget | Purpose |
|---|---|---|
| `"explorer"` | `DataTable(id="gems_table")` | Browse, filter, select gems |
| `"chat"` | `AgentChatTab` | Conversational agent with execution trace |
| `"export"` | `ExportTab` | Format selection, preview, file export |
| `"debug"` | `RichLog(id="debug-log")` | Application logging, diagnostics |

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L56-L63), [tui.py](src/rubygemdb/ui/tui.py#L1040-L1066)

### Keyboard Binding System

The TUI uses Textual's declarative `BINDINGS` class variable for 14 keyboard shortcuts, all mapped to `action_*` methods:

| Key | Action | Method |
|---|---|---|
| `q`, `ctrl+q`, `ctrl+c`, `escape` | Quit | `action_quit` |
| `r` | Refresh | `action_refresh` |
| `c` | Open Chat tab | `action_open_chat` |
| `space` | Toggle selection | `action_toggle_selection` |
| `a` | Select all filtered | `action_select_all` |
| `n` | Clear selection | `action_clear_selection` |
| `u` | Bulk Context7 update | `action_bulk_update_context7` |
| `x` | Export selected | `action_export_selected` |
| `e` | Edit gem | `action_edit_gem` |
| `d` | Delete gem | `action_delete_gem` |
| `plus` | Add gem | `action_add_gem` |
| `t` | Switch tab | `action_switch_tab` |

The `action_switch_tab` method implements tab cycling: `Explorer → Chat → Export → Debug → Explorer`.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L803-L821)

---

## Core Widgets & Tab Implementations

### 1. Explorer Tab: DataTable + Sidebar + Filtering

The explorer tab is the primary workspace. It consists of three interacting components:

**Gem DataTable** — A `DataTable` with five columns: Name, Category, Source URI, Context7 ID, Description. It supports row-based cursor navigation (`cursor_type = "row"`), zebra stripes, and row-level selection tracking.

**Classification Filter** — A `RadioSet` in the sidebar lists all 12 categories plus "All Categories". When a radio button is selected, the `on_filter_changed` handler triggers `update_table()`, which re-filters `self.all_gems` against the active classification using `g.classification.primary != target_cls`. The filtered list is sorted alphabetically and re-populated into the DataTable.

**GemDetails Sidebar** — A `Vertical` container (initially `display: none`) that slides into view when a DataTable row is selected via `DataTable.RowSelected`. It displays:
- Gem name (formatted with `[b]bold[/b]` markup)
- Classification primary category and confidence score
- Sub-categories list
- Source code URI (blue) and Context7 ID (green)
- Risk scores: invasiveness (/5), coupling (/4), abstraction leak
- Full description rendered as Markdown
- Runtime dependencies as a `ListView` (clickable for dependency-specific cheatsheet fetching)
- Action buttons: Fetch Cheatsheet, Update Context7 ID, Edit Category, Remove Gem, Fetch Combined Cheatsheet, Close

The dependency selection handler (`on_dependency_selected`) updates the target name on the "Fetch Cheatsheet" button, enabling targeted documentation retrieval for a specific dependency rather than the parent gem.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L65-L175), [tui.py](src/rubygemdb/ui/tui.py#L1174-L1243), [tui.py](src/rubygemdb/ui/tui.py#L1110-L1146)

### 2. Multi-Selection System

The selection system uses thread-safe operations via `self._selection_lock` (a `threading.Lock()`) and a Python `set` of gem names in `self._selected_gems`. Key operations:

**Toggle** — Uses `table.coordinate_to_cell_key()` to extract the current row's gem name from the cursor position, then adds/removes it from the set. Both `action_select_all` and `action_clear_selection` operate on the currently filtered gem list.

**Bulk Context7 Update** — The `action_bulk_update_context7` method copies selected gem names into `self._bulk_update_queue`, then processes them one-at-a-time via `_process_next_context7()`. For each gem, it calls `c7_service.search_libraries()`, pushes a `C7SelectionScreen` modal dialog, and on selection persists the ID via `storage.update_gem_metadata()`. This sequential processing pattern allows the user to abort mid-operation via `_bulk_update_aborted`.

**Selection Status** — The `_update_selection_status` method displays a notification with the current count, called after every selection change.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L1248-L1330)

### 3. Modal Screens (Dialog System)

The TUI implements five `ModalScreen` subclasses for user interaction:

| Screen | Purpose | Returns |
|---|---|---|
| `C7SelectionScreen` | Select from Context7 search results or manually enter a library ID | `str` (library ID) or `None` |
| `AddGemScreen` | Enter gem name to add to inventory | `str` (gem name) or `None` |
| `EditGemScreen` | Modify category (RadioSet), source URI, Context7 ID | `tuple(category, source_uri, c7_id)` or `None` |
| `ConfirmDeleteScreen` | Confirm gem deletion | `bool` |
| `ProjectFolderScreen` | Select/create Trackboi board folder | `str` (folder path) or `None` |
| `DistillSelectionScreen` | Select multiple options from the agent's response | `list[str]` (selected options) or `None` |

Each modal screen follows a consistent pattern: `compose()` builds the dialog layout, `@on(Button.Pressed, ...)` handlers capture user input, and `self.dismiss(result)` returns the result to the caller via callback.

The `EditGemScreen` demonstrates sophisticated state reconstruction: it pre-selects the current gem's category in a `RadioSet` by comparing `cat == self.gem.classification.primary`, and populates `Input` widgets with existing source URI and Context7 ID values.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L178-L380), [tui.py](src/rubygemdb/ui/tui.py#L1191-L1258)

### 4. Agent Chat Tab

The `AgentChatTab` (also extending `ScrollableContainer`) provides a split-pane conversational interface:

```
┌──────────────────┬──────────────────┐
│ Chat Main (70%)  │ Trace Panel (30%)│
│                  │                  │
│ RichLog          │ RichLog          │
│ (chat-log)       │ (trace-log)      │
│                  │                  │
│ Select (project) │                  │
│ Input (query)    │                  │
│ Buttons:         │                  │
│ Export | Clear | │                  │
│ Distill          │                  │
└──────────────────┴──────────────────┘
```

**Agent Initialization** — On mount, `load_agent()` is spawned in a background thread via `@work(thread=True)`. It calls the `agent_loader_callback` (which invokes `GemApp.get_txtai_agent()`) to lazily initialize the `TxtaiAgent` instance. Concurrently, `load_projects()` shells out to the `codebase-memory-mcp` CLI tool to populate a `Select` widget with available project contexts.

**Query Processing** — When the user submits a query, the system:
1. Captures the optional project context from the Select widget
2. Prepends `[Target Project Context: ...]` to the query if a project is selected
3. Checks if the agent is currently processing (`_thinking` flag); if so, queues the query
4. Spawns a status-updater thread that displays execution phase messages (e.g., "Tokenizing semantic input...", "Activating 'Other Steve' query expansion...")
5. Processes the query through `self.agent.run(query)`
6. Attempts to parse agent dictionary output using `ast.literal_eval` to reconstruct structured markdown
7. Handles Trackboi distillation if the query is prefixed with `[DISTILL_TRACKBOI <folder>]`

**Chat Export** — The `_export_chat_to_markdown` method serializes the entire `_chat_messages` list (which stores role, content, and timestamp per message) to a markdown file in `output/chat_exports/`.

**Trackboi Distillation Workflow** — The `on_distill_chat` button handler implements a multi-step workflow:
1. Scans chat history in reverse to find the last agent message
2. Parses the message for structured User Stories/Options using regex patterns
3. Falls back to extracting markdown headers if no explicit options found
4. Pushes `ProjectFolderScreen` to select/create a Trackboi board
5. Pushes `DistillSelectionScreen` for option selection
6. For each selected option, constructs a query prefixed with `[DISTILL_TRACKBOI <folder>]` and sends it to the agent

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L395-L795), [tui.py](src/rubygemdb/ui/tui.py#L1684-L1758)

### 5. Export Tab

The `ExportTab` provides format selection with live preview:

**Export Formats** — A `RadioSet` offers four formats: Gemfile, CSV, JSON, Markdown. The `RadioSet.Changed` event triggers `update_export_preview()`, which calls `_generate_export_preview()` to produce a preview limited to the first 5 gems.

**Preview Generation** — Each format generates an appropriate preview:
- **Gemfile**: Ruby syntax with `source` directive and `gem` declarations
- **CSV**: Header row with Name, Category, Source URI, Context7 ID, Description
- **JSON**: Array of objects with all fields
- **Markdown**: Table with aligned columns

**Export Execution** — The `on_export_pressed` handler validates the path, maps the format ID to an export function (`_export_gemfile`, `_export_csv`, `_export_json`, `_export_markdown`), and writes the file. Missing gems are detected and reported.

**Gem Sync** — When the Export tab is activated (via `TabbedContent.TabActivated`), the `set_gems` method is called with the current selection, updating the summary label and preview.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L310-L488), [tui.py](src/rubygemdb/ui/tui.py#L1397-L1475)

### 6. Debug Tab

A simple `RichLog` widget configured with `auto_scroll=True`. The `GemApp` class exposes four logging methods — `log_debug`, `log_info`, `log_error`, `log_warning` — all writing to `self.debug_log` with color-coded severity prefixes (`[dim]`, `[blue]`, `[red]`, `[yellow]`). These are called throughout the application for observability, with the `handle_error` method also capturing full Python tracebacks.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L1095-L1130), [tui.py](src/rubygemdb/ui/tui.py#L1153-L1171)

---

## Background Data Loading & Classification Pipeline

The three-phase data loading process in `load_data()` runs in a background thread via `@work(thread=True, exclusive=True)`:

**Phase 1: Immediate Load** — `storage.load_classified_gems()` fetches all previously classified gems from SQLite. The DataTable is updated immediately so the user sees results instantly, even while subsequent phases run.

**Phase 2: Inventory Sync** — If a CSV path was provided at launch and the inventory hasn't been loaded yet, `storage.load_inventory(inventory_path)` populates the SQLite inventory table. The exclusive worker flag ensures concurrent manual gem additions (via `add_gem_worker`) don't cause race conditions.

**Phase 3: Classification** — Uses a raw SQL query to find inventory gems that have no entry in `classified_gems`:
```sql
SELECT name, category, homepage, source_code_uri, context7_id 
FROM inventory 
WHERE name NOT IN (SELECT name FROM classified_gems)
```
Each unclassified gem is processed through `classifier.classify()`. Results are batch-saved every 5 gems for incremental persistence, and the table is refreshed to provide visible progress. The classification logic can optionally leverage LLM augmentation when heuristic confidence is low.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L1153-L1214)

---

## CSS Styling Architecture

The TUI uses Textual's inline CSS for layout, defined as a class variable on `GemApp`. Key design decisions:

- **Responsive sizing**: The sidebar is fixed at 30 columns, the details sidebar at 50 columns, with `height: 1fr` on the main content areas to consume remaining space
- **Visibility toggling**: The details sidebar starts with `display: none` and is shown/hidden programmatically via `sidebar.display = True/False`
- **Modal dialogs**: Use `align: center middle` with a thick accent border for visual prominence
- **Consistent section styling**: `.section-title` (bold, accent color), `.section-content` (bottom margin), `.help-text` (muted italic) classes enforce visual consistency across tabs
- **Button layout**: Action buttons within Horizontal containers use `width: 50%` for paired buttons and `width: 1fr` for balanced multi-button rows

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L822-L1014)

---

## Threading & Concurrency Model

The application employs three concurrency patterns:

1. **`@work(thread=True)` decorator** — Used for `load_data`, `add_gem_worker`, `lookup_c7_id`, `fetch_cheatsheet`, `fetch_combined_cheatsheet`, and agent query processing. These run in background threads managed by Textual's worker pool.

2. **`self.call_from_thread()` calls** — All UI mutations (table updates, notifications, logging) from background threads are marshaled back to the main thread using this method, preventing widget state corruption.

3. **`threading.Lock` for selection state** — `self._selection_lock` protects the `_selected_gems` set, `_bulk_update_queue` list, and `_bulk_update_aborted` flag from concurrent access during bulk operations.

4. **`_thinking` flag + `_query_queue`** — The AgentChatTab uses a boolean flag and a list-based queue to serialize agent queries. If a query is submitted while the agent is processing, it's queued and processed in order after completion.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L1022-L1035), [tui.py](src/rubygemdb/ui/tui.py#L565-L620), [tui.py](src/rubygemdb/ui/tui.py#L1520-L1615)

---

## Key Implementation Patterns

### Cleanup & Lifecycle Management

The `action_quit` method explicitly sets `_bulk_update_aborted = True` before calling `self.exit()`, with a fallback `self.exit(1)` if the initial exit raises an exception. This ensures ongoing bulk operations don't leave the database in an inconsistent state.

### Error Handling Centralization

The `handle_error` method provides a single point for error logging, user notification, and traceback capture. Every `try/except` block across the TUI delegates to this method rather than duplicating notification logic.

### Lazy Agent Initialization

The `TxtaiAgent` is not created during `GemApp.__init__()` but only when `get_txtai_agent()` is first called (via the agent chat tab or `action_open_chat`). This avoids loading the embeddings model and smolagents LLM during startup, keeping initial launch fast.

### Defensive Pattern for Data Integrity

When saving classification results after manual gem addition, the system both persists to SQLite and updates the in-memory `self.all_gems` list, ensuring immediate UI response without waiting for a full database reload.

**Sources**: [tui.py](src/rubygemdb/ui/tui.py#L1759-L1775), [tui.py](src/rubygemdb/ui/tui.py#L1074-L1093), [tui.py](src/rubygemdb/ui/tui.py#L1684-L1690)

---

## Suggested Next Steps

- For the classification pipeline that feeds the TUI's filter system, see [Heuristic Rule-Based Classification](13-heuristic-rule-based-classification)
- To understand the storage layer that backs all TUI data operations, see [SQLite Storage with Metadata Verification](11-sqlite-storage-with-metadata-verification)
- For the agent architecture integrated into the Chat tab, see [txtai-Based Vector Embeddings & Indexing](20-txtai-based-vector-embeddings-and-indexing)
- To explore the export functionality in depth, see [Gemfile, CSV, JSON & Markdown Export](24-gemfile-csv-json-and-markdown-export)