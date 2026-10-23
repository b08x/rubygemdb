The embedding and semantic search subsystem is the cognitive backbone of RubyGemDB — it transforms structured gem metadata into a queryable vector space that powers both the agent's contextual awareness and the TUI's semantic chat feature. At its core lies the `TxtaiAgent` class, a sophisticated wrapper around the [txtai](https://neuml.github.io/txtai/) embeddings library that orchestrates index construction, hybrid search (dense + sparse), dynamic memory indexing, and multi-query expansion. This component lives entirely within [`src/rubygemdb/agent.py`](src/rubygemdb/agent.py) (564 lines) and is activated through two integration points: the CLI `--embed` flag in the batch pipeline and the TUI's interactive chat screen.

Sources: [`src/rubygemdb/agent.py#L84-L564`](src/rubygemdb/agent.py#L84-L564), [`src/rubygemdb/cli.py#L250-L256`](src/rubygemdb/cli.py#L250-L256), [`src/rubygemdb/ui/tui.py#L1058-L1060`](src/rubygemdb/ui/tui.py#L1058-L1060)

## Embeddings Configuration & Initialization

The `TxtaiAgent` constructor creates a single `Embeddings` instance configured for hybrid retrieval with sqlite-vec as the vector store backend. The embedding model is specified via the `ollama/embeddinggemma` path, which instructs txtai to load a Gemma-based embedding model through Ollama's local inference API — this means all vector computation happens on the developer's machine using a GGUF quantized model served by `ollama`, with no external API calls for embedding generation. The configuration uses `content=True` to store full document dictionaries alongside their vectors (enabling rich metadata retrieval), `hybrid=True` to combine dense semantic vectors with sparse keyword signals, and `scoring={"method": "bm25", "normalize": False}` for the sparse component. The `fusion="rrf"` parameter enables Reciprocal Rank Fusion, which merges dense and sparse result lists by re-ranking based on reciprocal rank positions — this is essential when BM25 scores are unnormalized and cannot be directly averaged with cosine similarity scores.

Sources: [`src/rubygemdb/agent.py#L97-L106`](src/rubygemdb/agent.py#L97-L106)

```python
self.embeddings = Embeddings(
    path="ollama/embeddinggemma",
    backend="sqlite",
    content=True,
    hybrid=True,
    scoring={"method": "bm25", "normalize": False},
    fusion="rrf"
)
```

A critical architectural decision is the use of a **module-level `_embeddings_ref` variable** that mirrors `self.embeddings`. This exists because the smolagents framework introspects tool functions at the module level (via the `@tool` decorator), and the `search_memory_tool` needs to access the embeddings index from outside the `TxtaiAgent` instance. The reference is wired in the constructor and checked defensively in `_execute_search_with_expansion`. This is a pragmatic workaround for the impedance mismatch between txtai's instance-oriented API and smolagents' function-oriented tool introspection.

Sources: [`src/rubygemdb/agent.py#L80-L82`](src/rubygemdb/agent.py#L80-L82), [`src/rubygemdb/agent.py#L112-L114`](src/rubygemdb/agent.py#L112-L114), [`src/rubygemdb/agent.py#L346-L348`](src/rubygemdb/agent.py#L346-L348)

The index is persisted to `data/txtai/rubygems_index` (configured through `self.txtai_dir`). On initialization, the agent checks for an existing index file: if present, it loads via `self.embeddings.load(self.index_path)` and prints confirmation; if absent (or if `force_rebuild=True`), it proceeds to build the index from the SQLite database. The directory is created idempotently via `os.makedirs(self.txtai_dir, exist_ok=True)`.

Sources: [`src/rubygemdb/agent.py#L86-L88`](src/rubygemdb/agent.py#L86-L88), [`src/rubygemdb/agent.py#L109-L109`](src/rubygemdb/agent.py#L109-L109), [`src/rubygemdb/agent.py#L121-L125`](src/rubygemdb/agent.py#L121-L125)

## Index Construction from SQLite Inventory

The index construction pipeline in `_initialize_index()` reads from the SQLite `inventory` table — the same table managed by the [SQLite Storage with Metadata Verification](11-sqlite-storage-with-metadata-verification) layer — and streams documents into txtai's index. The process begins by querying `SELECT name, homepage, source_code_uri, context7_id, description FROM inventory` from the SQLite database at `data/rubygemdb.sqlite`. A separate `gem_cache.json` file (cached from the [RubyGems.org API Client](17-rubygems-org-api-client-with-caching)) is consulted for the `version_created_at` timestamp, which feeds the recency scoring component.

Sources: [`src/rubygemdb/agent.py#L121-L177`](src/rubygemdb/agent.py#L121-L177)

Each gem description undergoes **text chunking** via the `create_child_chunks()` function, which implements a two-level splitting strategy: first by paragraph boundaries (double newlines), then by sentence boundaries (`.!?` followed by whitespace). Chunks are capped at 512 characters to fit typical embedding model context windows while preserving semantic coherence. The chunker is a pure function with no state, making it testable and reusable — it is also employed by the `index_memory()` method for dynamic content.

Sources: [`src/rubygemdb/agent.py#L16-L52`](src/rubygemdb/agent.py#L16-L52)

```python
def create_child_chunks(text: str, max_chars: int = 512) -> list[str]:
    # Splits by paragraphs then sentences, respecting max_chars boundary
```

The document schema passed to txtai includes seven fields, each serving a distinct retrieval purpose:

| Field | Purpose | Used In |
|-------|---------|---------|
| `id` | Unique identifier (`{gem_name}_{chunk_idx}`) | Deduplication, upsert |
| `text` | Embedding source text (`Gem Name: {name}\nDescription: {chunk}`) | Vector retrieval |
| `name` | Gem name for filtering | Search result display |
| `source_code_uri` | Repository URL | Result enrichment |
| `context7_id` | Documentation reference | Cross-system linking |
| `parent_text` | Full original description for context recall | LLM context injection |
| `type` | Content type discriminator (`"gem"`, `"chat_user"`, `"chat_agent"`, `"context7"`) | Filtering |
| `updated_at` | ISO 8601 timestamp from RubyGems API | Recency boosting |

Sources: [`src/rubygemdb/agent.py#L155-L173`](src/rubygemdb/agent.py#L155-L173)

The gem name is **prepended** to the chunk text in the `"text"` field (`f"Gem Name: {gem_name}\nDescription: {child_text}"`) so that the embedding vector captures both identity and content — this ensures that queries mentioning a gem name by itself can still match via semantic similarity, even if the description doesn't use the exact name. The `parent_text` field preserves the full description for context window injection during LLM reasoning.

Data is streamed into txtai via a generator function (`stream_data()`), which yields documents one at a time. This is memory-efficient for large inventories and allows txtai to begin indexing before all data is materialized. After indexing, the index is persisted to disk and a summary count is printed.

Sources: [`src/rubygemdb/agent.py#L146-L176`](src/rubygemdb/agent.py#L146-L176)

## Dynamic Memory Indexing

Beyond the initial inventory index, the `TxtaiAgent` supports **runtime memory indexing** through `index_memory()`, which allows any text — chat interactions, Context7 documentation responses, or arbitrary metadata — to be chunked and upserted into the vector index on the fly. This method generates a UUID-based base identifier for each incoming text, splits it via the same `create_child_chunks()` function, and assigns a `doc_type` tag (`"chat_user"`, `"chat_agent"`, `"context7"`) for downstream filtering. A `timestamp` field is included to enable temporal filtering (though it is not currently used in scoring). After upserting, the index is automatically saved to disk, ensuring persistence across restarts.

Sources: [`src/rubygemdb/agent.py#L184-L212`](src/rubygemdb/agent.py#L184-L212)

This dynamic indexing is wired into the Context7 MCP integration: every time the agent calls a Context7 tool (e.g., fetching a library cheatsheet), the result is intercepted by a `forward_interceptor` wrapper that calls `self.index_memory(result, doc_type="context7", metadata={"source_tool": tool_name})`. This creates a **growing knowledge graph** where every documentation fetch enriches the vector store for future queries, enabling the agent to "remember" what it learned across conversation turns.

Sources: [`src/rubygemdb/agent.py#L248-L254`](src/rubygemdb/agent.py#L248-L254)

## Hybrid Search with Multi-Query Expansion

The `_execute_search_with_expansion()` method is the heart of the semantic retrieval pipeline. It implements a three-stage search strategy:

### Stage 1: Multi-Query Expansion via the "Other Steve" Persona

Before executing any search, the agent invokes its LLM (configured via `settings.rubygemdb_agent_model`) with a specialized system prompt — the "Other Steve" persona. This prompt, spanning roughly 100 lines of XML-structured directives, instructs the LLM to act as a prompt architect that **refactors** the user's natural language query into 2-3 discrete, concrete search queries. The prompt applies an "Anti-Slop Filter" that strips conversational padding ("please", "just", "simply"), destroys vibe words ("elegant", "robust"), eradicates metaphorical instructions ("craft", "weave"), and enforces literal output requirements. The LLM is asked to emit queries within `<query>` ... `</query>` XML tags, which are then extracted via regex and combined with the original query into a list of search variations.

Sources: [`src/rubygemdb/agent.py#L349-L453`](src/rubygemdb/agent.py#L349-L453)

```mermaid
flowchart LR
    A[User Query] --> B[Other Steve LLM Call]
    B --> C{Extract <query> tags}
    C -- Yes --> D[Augmented query list]
    C -- No --> E[Fallback: original query only]
    D --> F[SQL WHERE similar() for each query]
    F --> G[Deduplicate by parent_text/name]
    G --> H[Score: 70% semantic + 30% recency]
    H --> I[Top-k results]
```

### Stage 2: Vector Search Execution

Each expanded query is sanitized (single quotes escaped for SQL) and executed against the txtai index using `_embeddings_ref.search(sql)` with a SQL query pattern:

```sql
SELECT id, text, name, source_code_uri, context7_id, parent_text, type, updated_at, score 
FROM txtai WHERE similar('{safe_query}') LIMIT {limit * 4}
```

The `similar()` function is txtai's SQLite-vec integration point that performs the actual vector similarity search. The limit is multiplied by 4 (for `limit=5`, this means up to 20 candidates per query) to allow for cross-query deduplication and re-ranking. The `score` field returned by txtai represents the semantic similarity (cosine distance for dense, BM25 score for sparse, fused via RRF).

### Stage 3: Hybrid Scoring with Recency Boosting

The results from all query variations are aggregated into a single candidate pool, deduplicated by `name` (for gem documents) or `parent_text` (for chat/Context7 documents), and scored using a hybrid formula:

```
hybrid_score = (match_score × 0.7) + (recency_score × 0.3)
```

- **`match_score`**: The raw semantic similarity score from txtai's `similar()` function, already fused via RRF.
- **`recency_score`**: Calculated from the gem's `updated_at` timestamp. Gems updated in the current year get a perfect 1.0 score. For older gems, the score decays linearly: `1.0 - (years_old × 0.1)`, reaching 0.1 for gems 9 years old and 0.0 thereafter. This linear decay reflects the practical reality that Ruby gem maintenance tends to cluster around recent releases, while very old gems (pre-2015) are either abandoned or stable to the point where age is irrelevant.

The 70/30 weighting was chosen to prioritize semantic relevance while ensuring that modern alternatives to older gems surface competitively. Recency acts as a tiebreaker that also prevents the system from exclusively recommending decade-old "gold standard" gems when newer, equally capable alternatives exist. The final result set is sorted by hybrid score and truncated to the requested limit.

Sources: [`src/rubygemdb/agent.py#L456-L530`](src/rubygemdb/agent.py#L456-L530)

## Agent Runtime & Memory Management

The `run()` method orchestrates the full conversational loop. It first indexes the user's query as `doc_type="chat_user"`, then delegates to the smolagents `ToolCallingAgent` (lazily initialized in the `agent` property). The agent has access to four tool categories:

1. **`search_memory_tool`**: The semantic search tool described above, exposed to the LLM with a docstring that instructs it to use this tool *first* when asked to explore, design, or find Ruby gems.
2. **`fetch_rubygems_info`**: Direct RubyGems API lookup for detailed gem metadata.
3. **Context7 MCP tools**: Documentation search and retrieval tools from the [Context7 Documentation Search Service](18-context7-documentation-search-service).
4. **Codebase Memory MCP tools**: Restricted to `list_projects`, `get_architecture`, `search_code`, `search_graph`, and `trace_path` for target project awareness.

Sources: [`src/rubygemdb/agent.py#L218-L296`](src/rubygemdb/agent.py#L218-L296)

The agent is lazily initialized (not in `__init__` but in the `agent` property) to avoid loading the LLM model during index-only operations (`--embed`). After the agent returns a response, that response is indexed as `doc_type="chat_agent"`, creating a persistent memory of the conversation. Finally, a **sliding window** mechanism prevents context bloat: if the agent's memory contains more than 6 steps (approximately 3 user/agent exchanges), system prompts are preserved while all but the last 6 steps are trimmed. This keeps the LLM context window within operational boundaries while retaining the immediate conversational state.

Sources: [`src/rubygemdb/agent.py#L531-L565`](src/rubygemdb/agent.py#L531-L565)

## Integration Map

The embedding subsystem integrates with three other architectural components:

| Integration | File | Trigger | Purpose |
|------------|------|---------|---------|
| CLI Pipeline | [`src/rubygemdb/cli.py#L250-L256`](src/rubygemdb/cli.py#L250-L256) | `--embed` flag on `process` subcommand | Build or rebuild the txtai index after metadata verification and classification |
| TUI Chat | [`src/rubygemdb/ui/tui.py#L1058-L1060`](src/rubygemdb/ui/tui.py#L1058-L1060), [`src/rubygemdb/ui/tui.py#L1753-L1756`](src/rubygemdb/ui/tui.py#L1753-L1756) | Agent Chat tab (`"c"` keybinding) | Lazy-loads `TxtaiAgent` and pushes a modal chat screen |
| MCP Tool Layer | [`src/rubygemdb/agent.py#L248-L254`](src/rubygemdb/agent.py#L248-L254) | Context7 tool invocation | Auto-indexes documentation results into memory |

The CLI integration is the most straightforward: after Phases 1 and 2 of the batch pipeline (verification and classification), the `--embed` flag triggers `agent.build_index()`, which forces a full rebuild from the SQLite database. This ensures the vector index is always consistent with the classified inventory. The TUI integration uses a lazy singleton pattern (`get_txtai_agent()`) that creates the agent on first access and caches it for the app's lifetime.

Sources: [`src/rubygemdb/cli.py#L252-L256`](src/rubygemdb/cli.py#L252-L256), [`src/rubygemdb/ui/tui.py#L1753-L1756`](src/rubygemdb/ui/tui.py#L1753-L1756)

## Dependencies & Runtime Requirements

The txtai integration requires the Python packages specified in `pyproject.toml`: `txtai[agent,pipeline,similarity]>=9.13.0`, `sqlite-vec>=0.1.9`, `torch`, `torchvision`, and `torchaudio`. The extra brackets `[agent,pipeline,similarity]` ensure that txtai's agent orchestration, pipeline processing, and similarity search components are installed alongside the base library. The `sqlite-vec` package provides the vector extension for SQLite that txtai uses as its backend. PyTorch is sourced from a CPU-only index (`pytorch-cpu`) to avoid GPU dependencies. The `ollama` server must be running locally with the `embeddinggemma` model pulled — this is configured separately and is not managed by the Python dependency tree.

Sources: [`pyproject.toml#L16-L23`](pyproject.toml#L16-L23), [`pyproject.toml#L36-L40`](pyproject.toml#L36-L40)

## Next Steps

To understand how the expanded queries from the Other Steve prompt are generated and how the search results feed into agent reasoning, proceed to [Multi-Query Expansion with "Other Steve" Prompt](21-multi-query-expansion-with-other-steve-prompt). For the TUI surface that exposes this embedding system to interactive users, see [Textual-Based Interactive TUI Implementation](23-textual-based-interactive-tui-implementation). To understand how the SQLite database that feeds the index is populated and verified, see [SQLite Storage with Metadata Verification](11-sqlite-storage-with-metadata-verification).