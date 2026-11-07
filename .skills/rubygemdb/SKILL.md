---
name: rubygemdb
description: Query and manage the RubyGemDB gem knowledge base. Discover Ruby gems across 21 architectural categories, inspect gem metadata, dependencies, and risks, compose layered gem stacks for back-end tooling, execute containerized GraphRAG with grounded [C1] citations, and manage persistent exploration sessions.
---

# RubyGemDB

RubyGemDB is a specialized local knowledge base and architectural analysis system for Ruby gems. It organizes gems into **21 functional categories** across **4 stack layers** (`substrate`, `plumbing`, `composition`, `quality`) and **4 build phases** (`skeleton`, `input`, `processing`, `output`).

The persistent databases live inside the repository:
- **`data/rubygemdb.sqlite`**: Central gem catalog, verified inventory, manual overrides, and FTS5 search index.
- **`data/sessions.sqlite`**: Dedicated GraphRAG exploration sessions, conversational turns, citations, and execution traces.

---

## 3-Tier Routing Architecture

When interacting with RubyGemDB, use the appropriate tier based on your execution context and task requirements:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 1: rubygemdb-mcp Server (FastMCP stdio or background SSE daemon)   │
│ - Preferred when MCP tools are mounted in the host agent               │
│ - Read-only catalog search, gem info, stack composition               │
│ - Containerized GraphRAG indexing/queries & persistent sessions        │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼ (fallback or single-gem mutation)
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 2: Skill Script (~/.agents/skills/rubygemdb/scripts/gem.py)       │
│ - Fast CLI fallback when MCP tools are unavailable                     │
│ - Catalog mutations: add, remove, prefer, unprefer (MCP is mode=ro)    │
│ - Local GraphRAG & session management CLI subcommands                  │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼ (bulk operator lifecycle)
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 3: Repository Operator CLI (uv run rubygemdb ...)                 │
│ - Bulk maintenance: import-stars, prune, reclassify, cache,            │
│   build-indexer, install-skill                                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Tier 1 — Native MCP Server (`rubygemdb-mcp`)

When `rubygemdb-mcp` is mounted in your environment, prefer calling MCP tools directly. Catalog queries operate in strict read-only mode (`mode=ro`), preventing accidental catalog corruption while session tools manage conversation history in `data/sessions.sqlite`.

### Available MCP Tools (11 Tools)

#### Catalog & Exploration Tools
- **`search_gems(query: str, category: Optional[str] = None, phase: Optional[str] = None, limit: int = 5, use_vector: bool = True)`**:
  Fuses FTS5 keyword and optional semantic vector search with Reciprocal Rank Fusion (RRF). Supports filtering by category slug or build phase.
- **`get_gem(name: str)`**:
  Fetches full gem record: classification, confidence, layer, phase, `sub_categories`, `genai_uses`, architectural risks, runtime dependencies, source URL, and Context7 ID.
- **`compose_stack(query: str, max_gems: int = 10)`**:
  Synthesizes a curated, cohesive, layered gem stack manifest (substrate through quality) with contextual task roles and Gemfile snippet, capped by `max_gems`.
- **`list_categories(slug: Optional[str] = None)`**:
  Lists all 21 categories with layers, phases, and `sub_categories` facet slugs; when `slug` is provided, returns full details with example gems.
- **`build_gem_graphrag(gems: List[str], intention: Optional[str] = None, session_id: Optional[str] = None)`**:
  Triggers ephemeral containerized AST and documentation extraction for target gems, building the local GraphRAG vector index and initializing a persistent session.
- **`query_gem_graphrag(gems: List[str], query: str, intention: Optional[str] = None, session_id: Optional[str] = None)`**:
  Executes two-stage GraphRAG retrieval (vector match + 1-hop neighborhood), returning grounded markdown context, AST signatures, decode parameter types, and a top-level `citations` list with `[C1]` tags.

#### Session Management Tools
- **`list_rag_sessions(limit: int = 20)`**:
  Lists active GraphRAG exploration sessions ordered by recent activity.
- **`get_rag_session(session_id: str)`**:
  Retrieves session metadata, conversation turns, citations, and execution traces.
- **`resume_rag_session(session_id: str)`**:
  Pre-warms the session's gem index in txtai and returns the last 5 messages for rapid resumption.
- **`export_rag_session_transcript(session_id: str, format: str = "markdown")`**:
  Exports complete session transcript in Markdown or JSON format.
- **`delete_rag_session(session_id: str)`**:
  Permanently deletes a session and cascades through its message and trace records.

### MCP Resources
- **`rubygemdb://taxonomy`**: Complete reference of the 21 architectural categories, stack layers, and 80 sub-category facet vocabularies.
- **`rubygemdb://stack-seeds`**: Curated base gem seeds (`dotenv`, `drydock`, `pry`, `rubocop`, `journald-logger`, etc.) used to scaffold back-end stacks.

### MCP Prompts
- **`design-ruby-stack`**: Guided architectural prompt for generating structured gem stacks based on purpose and technical constraints.
- **`gem-architect-rag`**: Grounded GraphRAG prompt enforcing strict adherence to retrieved API signatures and `[C1]` citation formatting.

### Background SSE Daemon Lifecycle
To run the MCP server as a detached SSE daemon on the host:
```bash
uv run rubygemdb-mcp --daemon start --port 8011
uv run rubygemdb-mcp --daemon status
uv run rubygemdb-mcp --daemon stop
```
Artifacts are written to `data/mcp.pid`, `data/mcp.json`, and `data/mcp.log`.

---

## Tier 2 — Skill Script (`scripts/gem.py`)

When MCP is unmounted or when performing single-gem catalog mutations, use `scripts/gem.py`. Execute it using the rubygemdb project environment:

```bash
uv run --project /home/b08x/WorkspaceV3/rubygemdb \
    python ~/.agents/skills/rubygemdb/scripts/gem.py <subcommand> [args]
```
*(Abbreviated as `gem.py` below).*

### Catalog Search & Inspection
```bash
gem.py search "pdf parser" --category document_parsing --limit 5
gem.py search "terminal ui" --phase input --expand --rerank --json
gem.py info nokogiri --json
```
- `--category <slug>` restricts search to one of the 21 slugs.
- `--expand` triggers query expansion via local GGUF model into lexical/vector variations.
- `--rerank` reranks candidate pools with a cross-encoder before truncation.
- `info` surfaces primary category, `sub_categories` facets, `genai_uses`, risks, and dependencies.

### Stack Composition
```bash
gem.py stack "CLI data pipeline tool" --max-gems 6
gem.py stack "event sourcing engine" --out /tmp/stack/
```
Generates a curated layered manifest (`stack-manifest.yaml`) and advisory `Gemfile`.

### Single-Gem Catalog Mutations (Tier 2 Exclusive)
Because MCP catalog tools run with SQLite `mode=ro`, use `gem.py` for mutating individual gem entries:
```bash
# Add gem from rubygems.org and classify into taxonomy (requires network)
gem.py add ruby-openai fast-mcp

# Remove gem from inventory and classification index
gem.py remove old-gem-name

# Mark/unmark gems as user-preferred (boosts ranking in search and stacks)
gem.py prefer sidekiq pg
gem.py preferred
gem.py unprefer sidekiq
```

### Taxonomy & Statistics
```bash
gem.py categories             # List all 21 categories with sub-category facets
gem.py categories ai_llm      # Detailed category inspection: keywords, base gems, facet examples
gem.py stats --json           # Gem distribution counts across layers and categories
```

### GraphRAG & Session Management
```bash
# Build/load GraphRAG index for target gems
gem.py graphrag build thor yajl-ruby --intention "CLI parser" --session-id "uuid"

# Query GraphRAG for grounded context and [C1] citations
gem.py graphrag query "How to define options?" --gems thor --top-k 5 --json

# Manage persistent sessions
gem.py sessions list --limit 10
gem.py sessions show <session_id>
gem.py sessions export <session_id> --format markdown --out transcript.md
gem.py sessions delete <session_id>
```

---

## Tier 3 — Repository Operator CLI (`uv run rubygemdb`)

Use the main repository CLI for bulk data maintenance, vector index rebuilding, pruning audits, and skill installation.

```bash
cd /home/b08x/WorkspaceV3/rubygemdb
```

### 1. Import Starred Repositories (`import-stars`)
Fetch starred Ruby repositories from GitHub, match with published RubyGems, enrich metadata, infer `genai_uses`, and perform prune audits:
```bash
uv run rubygemdb import-stars --dry-run                    # Preview without modifying database
uv run rubygemdb import-stars --classify --embed           # Full import, classify into 21 categories, vectorize
uv run rubygemdb import-stars --select-prune              # Interactively select audit candidates to prune
uv run rubygemdb import-stars --apply-prune               # Auto-prune stale/overlapping audit gems
```

### 2. Interactive Pruning (`prune`)
Audit and prune stale (no release in 3+ years) or name-overlapping gems using dual-engine selection (`gum choose` or Textual `SelectionList`):
```bash
uv run rubygemdb prune                                     # Launch interactive multi-selector
uv run rubygemdb prune data/prune-report.md --all          # Prune all candidates listed in report
uv run rubygemdb prune --backend textual                   # Force Textual UI backend
uv run rubygemdb prune --backend gum                       # Force Charmbracelet Gum backend
uv run rubygemdb prune --prune-list prune.txt              # Prune approved gems from text file
```

### 3. Fresh Reclassification (`reclassify`)
Wipe and rebuild all classification data and vectors under the current taxonomy:
```bash
uv run rubygemdb reclassify --select-prune --embed         # Interactive prune before reclassification
uv run rubygemdb reclassify --apply-prune --embed          # Non-interactive fresh start
uv run rubygemdb reclassify --clear-embeddings-cache       # Also purge and vacuum universal vector cache
```

### 4. Vector Embedding Cache Management (`cache`)
Manage the shared SQLite vector cache (`data/cache/embeddings_cache.sqlite`):
```bash
uv run rubygemdb cache stats                               # Display table of vector counts and disk size
uv run rubygemdb cache clear                               # Wipe all vectors and vacuum disk space
uv run rubygemdb cache prune --max-age-days 90             # Prune vectors older than 90 days
```

### 5. Skill Deployment (`install-skill`)
Deploy or symlink the repository skill to the agent skills folder:
```bash
uv run rubygemdb install-skill                             # Copy to ~/.agents/skills/rubygemdb
uv run rubygemdb install-skill --link                      # Symlink to ~/.agents/skills/rubygemdb
uv run rubygemdb install-skill --target /path/to/skills    # Custom destination directory
```

### 6. Build GraphRAG Container Image (`build-indexer`)
Rebuild the Podman/Docker image used for AST symbol and documentation extraction:
```bash
uv run rubygemdb build-indexer
```

---

## Containerized GraphRAG & `[C1]` Citation Contract

RubyGemDB uses an isolated container environment (`rubygemdb-indexer:latest`) to extract rich AST symbols (`rubydex`) and structured documentation tags (`decode`) from shallow-cloned repositories or `.gem` archives.

### Retrieval Pipeline
1. **Shallow Clone / Unpack**: Repositories are fetched into `data/cache/gems/`.
2. **Container Indexer**: `extract_graph.rb` extracts classes, methods, superclasses, parameters (`@parameter`), return types (`@returns`), and verified code examples (`@example`).
3. **Two-Stage Retrieval**:
   - Stage 1: Semantic search across AST and docstring nodes via `txtai`.
   - Stage 2: 1-hop graph neighborhood expansion linking methods to doc tags and classes.
4. **Grounded Prompt Context**: The synthesized context formats symbols with sequential citation tags (e.g. `[C1]`, `[C2]`).

### Grounded Citation Contract for AI Agents
When generating implementation plans or code examples from GraphRAG context:
1. **Inline Citations**: Every method, class, or code example must be cited inline using its assigned tag and source file (e.g., `[C1] (File: lib/thor.rb)`).
2. **Strict API Adherence**: Use **only** methods, classes, and argument signatures that explicitly appear in the citations.
3. **Explicit Custom Logic**: If a necessary capability is not provided by the retrieved APIs, explicitly declare the limitation and provide custom application Ruby clearly separated from gem capabilities.
4. **Closing Summary**: Conclude answers with a `### Citations` summary table mapping each cited tag to its gem, file, and symbol.

---

## Environment Variables & Configuration

Configure these environment variables in your `.env` or shell environment:

```bash
# LLM Provider Key (Default: OpenRouter)
export OPENROUTER_API_KEY="your_openrouter_key"    # Used for fallback classification and stack curation
# export MISTRAL_API_KEY="your_mistral_key"        # Used only when RUBYGEMDB_PROVIDER=mistral

# Context7 Documentation & Verification
export CONTEXT7_API_KEY="your_context7_key"        # For cheatsheet generation and library ID checks

# GitHub API Token (Optional)
export GITHUB_TOKEN="your_github_token"            # Raises rate limits for import-stars

# Optional Local Retrieval Enhancement (Off by default)
export QUERY_EXPANSION_ENABLED="true"              # Local GGUF query expansion (mradermacher/qmd-query-expansion-1.7B)
export RERANKER_ENABLED="true"                     # Local GGUF cross-encoder reranking (Qwen3-Reranker-0.6B)

# Embedding Cache Retention Policies
export EMBEDDINGS_CACHE_MAX_ENTRIES="100000"       # Max cached vectors before pruning (default: 100k)
export EMBEDDINGS_CACHE_TTL_DAYS="180"             # Vector retention in days (default: 180)

# GraphRAG Agent Response Cap
export AGENT_MAX_TOKENS="4096"                     # Max tokens for GraphRAG agent responses (default: 4096)
```
