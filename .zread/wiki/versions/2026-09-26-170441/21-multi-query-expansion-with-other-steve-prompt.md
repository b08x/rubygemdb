The Multi-Query Expansion with "Other Steve" Prompt is a retrieval augmentation technique embedded within the `TxtaiAgent` class that transforms a single user query into multiple semantically diverse search queries, executes them against a hybrid vector + keyword index, then fuses the results through a weighted scoring system. This addresses the fundamental limitation of single-query retrieval: the original query may use imprecise terminology, omit critical facets, or fail to align with the vocabulary of the indexed documents. The "Other Steve" persona — a fictional Senior Staff Engineer functioning as a Ruby Prompt Architect — serves as the prompt-rewriting engine that applies Systemic Functional Linguistics (SFL) methodology to decompose vague user intents into concrete, search-optimized query fragments.

## The "Other Steve" Persona System

The "Other Steve" persona is defined as a self-contained XML-style system prompt of approximately 200 lines embedded directly inside the `_execute_search_with_expansion` method at [agent.py](src/rubygemdb/agent.py#L349-L448). It is not a separate service or class; it is an inline prompt template sent to the LLM (the `LiteLLMModel` instance configured via `settings.rubygemdb_agent_model`, defaulting to `mistral/mistral-small-latest`) to reframe the user's original query before semantic search occurs. The prompt is structured around three core directives:

| Directive | Purpose | Applied To |
|---|---|---|
| **Anti-Slop Filter** | Strips conversational padding ("please", "kindly"), vibe words ("elegant", "robust"), metaphorical instructions ("craft", "weave"), ambiguous scope ("handle edge cases"), redundant comments, and enforces literal output requirements | User's original prompt text |
| **SFL Compiler** | Decomposes the intent into three Systemic Functional Linguistics metafunctions: **Field** (compute task, entities, processes, constraints), **Tenor** (persona, audience, constraints), **Mode** (output schema, formatting, length) | Refactored task decomposition |
| **Conciseness Engine** | Eliminates needless words, uses positive statements, and grounds instructions in measurable reality (e.g., O(1) time complexity) | Final rewritten output |

The execution protocol within the persona prompt follows a four-step process: **Diagnostic** (2–3 sentence teardown of the original prompt), **SFL Breakdown** (Field/Tenor/Mode decomposition), **Refactored Tasks** (discrete task separation), and finally **XML Output** where the model is instructed to emit 2–3 `<query>...</query>` tags containing the best semantic search queries for the Ruby gems database. This XML extraction mechanism at [agent.py](src/rubygemdb/agent.py#L456-L460) is the critical bridge between the prompt rewriting step and the multi-query execution:

```python
extracted_queries = re.findall(r'<query>(.*?)</query>', expansion_response, re.DOTALL)
if extracted_queries:
    queries = [query] + [q.strip() for q in extracted_queries]
else:
    queries = [query]
```

The original query is always prepended as the first entry, ensuring the user's original intent is never discarded. If the LLM fails to produce parseable `<query>` tags, the system degrades gracefully to single-query mode.

## Architecture and Data Flow

The `_execute_search_with_expansion` method at [agent.py](src/rubygemdb/agent.py#L345-L529) orchestrates a three-stage pipeline that integrates tightly with the txtai `Embeddings` instance and the SQLite-vec backend.

```mermaid
flowchart TD
    A[User Query] --> B[Other Steve LLM Call<br/>LiteLLMModel + Mistral]
    B --> C[XML Parsing via regex<br/>Extract <query> tags]
    C --> D{Multi-Query Set<br/>(Original + 2-3 expansions)}
    D --> E[Execute SQLite-vec<br/>similar() for each query]
    E --> F[Deduplication by parent_text/name]
    F --> G[Hybrid Scoring<br/>70% semantic + 30% recency]
    G --> H[Sort by hybrid_score<br/>Limit to top-k]
    H --> I[JSON response to<br/>search_memory_tool caller]

    style A fill:#e1f5fe,stroke:#0288d1
    style B fill:#fff3e0,stroke:#e65100
    style D fill:#e8f5e9,stroke:#2e7d32
    style G fill:#fce4ec,stroke:#c62828
```

**Data flow details:**

1. **LLM Expansion Phase**: The `LiteLLMModel` instance (lazy-initialized in the `agent` property at [agent.py](src/rubygemdb/agent.py#L196-L200)) receives the system prompt and the user query. The model's `temperature` is not explicitly set in this call, meaning it inherits the default from `LiteLLMModel` (which is typically 0.7 for Mistral models), deliberately introducing controlled variability in the generated queries.

2. **SQLite-vec Batched Execution**: Each query in the expanded set is executed via `_embeddings_ref.search(sql)` using a SQL query template at [agent.py](src/rubygemdb/agent.py#L463-L465):
   ```sql
   SELECT id, text, name, source_code_uri, context7_id, parent_text, type, updated_at, score 
   FROM txtai WHERE similar('{safe_query}') LIMIT {limit * 4}
   ```
   The `limit * 4` multiplier is intentional — it casts a wider net per query so the subsequent deduplication and scoring stage has sufficient candidates to rank.

3. **Deduplication Strategy**: A set of `unique_parents` keys is maintained across all query results. For gem-type documents, the dedup key is the `name` field; for all other document types (context7 documentation, chat history, indexed memory), the key is the `parent_text` string itself. This prevents the same gem or document from appearing multiple times across different query expansions.

## Hybrid Scoring and Recency Boost

The multi-query expansion alone would produce redundant or poorly-ranked results without a sophisticated scoring layer. The system implements a two-factor hybrid weighting scheme at [agent.py](src/rubygemdb/agent.py#L485-L502):

| Component | Weight | Source | Purpose |
|---|---|---|---|
| **Semantic Match Score** | 70% | `score` field from SQLite-vec similarity search | Measures vector + BM25 relevance to query |
| **Recency Score** | 30% | `updated_at` timestamp from gem_cache.json or 2000-01-01 fallback | Favors actively maintained gems |

The recency score calculation follows a linear decay function:
- **0 years old** (updated this year): `recency_score = 1.0`
- **1 year old**: `recency_score = 0.9`
- **2–9 years old**: `recency_score = 1.0 - (years_old * 0.1)` (0.8 → 0.1)
- **10+ years old**: `recency_score = 0.0` (years_old >= 10, no boost)

The `updated_at` field originates from the `gem_cache.json` file's `version_created_at` field, populated during the index build phase in `_initialize_index` at [agent.py](src/rubygemdb/agent.py#L143). If the timestamp is missing or unparseable, the fallback `2000-01-01T00:00:00.000Z` produces `years_old >= 10`, resulting in zero recency boost — effectively treating the gem as legacy without penalizing it through the semantic score.

The final `hybrid_score` computation:
```
hybrid_score = (match_score * 0.7) + (recency_score * 0.3)
```

Candidates are sorted by `hybrid_score` in descending order, and the top-k results (respecting the original `limit` parameter from the `search_memory_tool` call) are returned as a JSON array. Each result includes both component scores (`match_score` and `recency_score`) alongside the `hybrid_score` for transparency, as well as the specific `matched_query` that produced that candidate — enabling downstream debugging and observability.

## Integration with the Agent Search Pipeline

The multi-query expansion engine is surfaced to the smolagents `ToolCallingAgent` through a single tool wrapper defined at [agent.py](src/rubygemdb/agent.py#L260-L270):

```python
@tool
def search_memory_tool(query: str, limit: int = 5) -> str:
    """
    Primary semantic search engine. Use this tool FIRST when asked to explore, 
    design, or find Ruby gems for a specific use-case or functionality (e.g., 'NLP pipeline').
    It searches a vector database of Ruby gems, past chat history, and Context7 docs.

    Args:
        query: Natural language search query (e.g., 'fast web framework' or 'NLP text processing').
        limit: Maximum number of results to return (default 5).
    """
    return self._execute_search_with_expansion(query, limit)
```

The tool is registered alongside `fetch_rubygems_info`, Context7 MCP tools, and codebase memory MCP tools in the `ToolCallingAgent` constructor at [agent.py](src/rubygemdb/agent.py#L292). The agent's system prompt at [agent.py](src/rubygemdb/agent.py#L207-L256) explicitly instructs the model to use `search_memory_tool` **first** when exploring gem functionality, ensuring the multi-query expansion is triggered before any downstream tool calls.

The `run` method at [agent.py](src/rubygemdb/agent.py#L531-L564) wraps the entire flow with memory indexing — both the user query and the agent response are indexed into the txtai embeddings via `index_memory`, creating a feedback loop where past queries and responses become searchable in future sessions. A sliding window mechanism truncates the agent's memory to the last 6 steps (approximately 3 user/agent turns) to prevent context bloat.

## Design Rationale and Trade-offs

The "Other Steve" approach embodies several deliberate architectural decisions:

**Why an inline persona prompt instead of a dedicated microservice?** The persona is stateless and operates on a single LLM call per search invocation. Extracting it into a separate service would add IPC overhead (MCP or HTTP) for what is fundamentally a prompt transformation with a regex parser. The trade-off is that the expansion logic is tightly coupled to the LLM model configured for the agent — if the model is swapped to a non-instruction-tuned variant, the XML tag extraction could fail silently, falling back to single-query mode.

**Why regex-based XML extraction instead of structured output?** The smolagents `LiteLLMModel` does not expose structured output/JSON mode guarantees across all providers. The regex extraction with DOTALL flag at [agent.py](src/rubygemdb/agent.py#L456) provides provider-agnostic resilience — the model can emit the queries in any surrounding text format, and the extraction still succeeds. The fallback to the original query when no tags are found ensures the system never returns empty results due to parsing failure.

**Why 70/30 weighting for semantic vs. recency?** The 70% semantic weight reflects the primacy of relevance — a perfectly maintained but irrelevant gem should not rank higher than a moderately outdated but highly relevant one. The 30% recency weight acts as a tiebreaker and a gentle anti-decay signal. This weighting is hardcoded; it is not configurable through `Settings` or environment variables.

**The `limit * 4` multiplier in the SQL query** at [agent.py](src/rubygemdb/agent.py#L465) is calibrated for up to 4 queries (1 original + 3 expansions). With 4 queries each returning `limit * 4` candidates, the deduplication stage may process up to `4 * limit * 4 = 16 * limit` raw results before collapsing to the final top-k. For the default `limit=5`, this means up to 80 candidates are evaluated per search.

## Next Steps

To understand how the indexed documents are structured for retrieval, see [txtai-Based Vector Embeddings & Indexing](20-txtai-based-vector-embeddings-and-indexing). For the broader agent orchestration layer that consumes `search_memory_tool`, see [MCP Tool Integration for Context & Task Management](22-mcp-tool-integration-for-context-and-task-management). The LLM service that powers the Mistral API calls (distinct from this LiteLLM-based agent) is documented in [Mistral LLM Service with Response Caching](19-mistral-llm-service-with-response-caching).