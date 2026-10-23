# Implementation Plan: Query Expansion and Cross-Encoder Re-ranking

## Solution Approach
Enhance RubyGemDB's retrieval capabilities across both the gem catalog (`hybrid_search`) and ephemeral gem code exploration (`GraphRAGService`) by integrating local GGUF models via `llama_cpp` and `huggingface_hub`. 
1. **Query Expansion** uses `mradermacher/qmd-query-expansion-1.7B-GGUF` (default `qmd-query-expansion-1.7B.Q4_K_M.gguf`) with the Qwen3 `/no_think` prompt template to generate structured lexical (`lex:`) and vector (`vec:`) variations.
2. **Multi-Pass Fusion** executes parallel searches (lexical terms via SQLite FTS5, vector terms via txtai) and merges candidates using Reciprocal Rank Fusion (RRF) over an oversampled candidate pool (3x `top_k`, clamped 15-50).
3. **Cross-Encoder Re-ranking** uses `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` (`qwen3-reranker-0.6b-q8_0.gguf`) to compute cross-attention relevance scores between the query and candidate items, producing a calibrated final ranking.
4. **Lifecycle & Fallback**: Weights are lazily downloaded and cached on first call via `huggingface_hub.hf_hub_download`. If models are disabled, un-downloaded, or encounter runtime errors, both pipelines transparently fall back to baseline unexpanded/un-reranked retrieval.

---

## Ordered Implementation Steps

### Step 1: Update Project Dependencies in `pyproject.toml`
- **Files**: `pyproject.toml`, `uv.lock`
- **Actions**:
  - Add `llama-cpp-python>=0.3.0` and `huggingface_hub>=0.28.0` to `project.dependencies` in `pyproject.toml`.
  - Run `uv lock` to update `uv.lock` and ensure the dependency tree resolves cleanly.
- **Verification**:
  - Run `uv run python -c "import llama_cpp, huggingface_hub; print('llama_cpp & hf ready')"`
  - Fact verified: `fact-deps`

### Step 2: Add Config Settings & Environment Aliases in `src/rubygemdb/core/config.py`
- **Files**: `src/rubygemdb/core/config.py`
- **Actions**:
  - Add configuration attributes to `Settings`:
    - `query_expansion_enabled: bool = False` (aliases: `query_expansion_enabled`, `expander_enabled`)
    - `query_expander_repo_id: str = "mradermacher/qmd-query-expansion-1.7B-GGUF"`
    - `query_expander_filename: str = "qmd-query-expansion-1.7B.Q4_K_M.gguf"`
    - `query_expander_n_ctx: int = 1024`
    - `query_expander_n_threads: Optional[int] = None`
    - `reranker_enabled: bool = False` (aliases: `reranker_enabled`, `cross_encoder_enabled`)
    - `reranker_repo_id: str = "ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF"`
    - `reranker_filename: str = "qwen3-reranker-0.6b-q8_0.gguf"`
    - `reranker_n_ctx: int = 1024`
    - `reranker_n_threads: Optional[int] = None`
    - `reranker_candidate_multiplier: int = 3`
    - `models_cache_dir: Optional[Path] = None` (defaulting to `data/models`)
- **Verification**:
  - Run `uv run python -c "from rubygemdb.core.config import settings; assert settings.query_expander_repo_id == 'mradermacher/qmd-query-expansion-1.7B-GGUF'"`
  - Fact verified: `fact-config`

### Step 3: Implement GGUF Model Manager in `src/rubygemdb/services/model_manager.py`
- **Files**: `src/rubygemdb/services/model_manager.py`
- **Actions**:
  - Create `GGUFModelManager` singleton / service to handle lazy on-demand downloading of model files via `huggingface_hub.hf_hub_download` into `models_cache_dir`.
  - Cache active `llama_cpp.Llama` instances for query expansion and reranking to avoid reloading weights repeatedly.
  - Implement non-blocking exception handling: log warning on download or load failure and return `None` so callers can seamlessly fall back.
- **Verification**:
  - Unit tests mocking `hf_hub_download` and `Llama` initialization.
  - Fact verified: `fact-model-manager`, `fact-graceful-fallback`

### Step 4: Implement Query Expander Service in `src/rubygemdb/services/query_expander.py`
- **Files**: `src/rubygemdb/services/query_expander.py`
- **Actions**:
  - Create `QueryExpander` class taking optional `model_manager` or `settings`.
  - Format prompt using the Qwen3 chat template:
    `<|im_start|>user /no_think Expand this search query: {query}<|im_end|>\n<|im_start|>assistant\n`
  - Call `Llama.create_completion(..., max_tokens=128, temperature=0.1, stop=["<|im_end|>"])`.
  - Parse generated output lines into `QueryExpansion(lexical=[...], vector=[...], raw=...)`.
  - If disabled, unavailable, or upon exception, return `QueryExpansion(lexical=[], vector=[], raw="")`.
- **Verification**:
  - Unit tests with mock responses verifying prompt formatting, parsing of `lex:` / `vec:` lines, and fallback.
  - Fact verified: `fact-query-expander`, `fact-graceful-fallback`

### Step 5: Implement Cross-Encoder Reranker Service in `src/rubygemdb/services/reranker.py`
- **Files**: `src/rubygemdb/services/reranker.py`
- **Actions**:
  - Create `CrossEncoderReranker` class taking optional `model_manager` or `settings`.
  - Support scoring text pairs `(query, passage_text)`.
  - Construct instruction-aware Qwen3 reranking prompt:
    `<|im_start|>system\nJudge whether the Document meets the requirements based on the Query.<|im_end|>\n<|im_start|>user\n<Query>: {query}\n<Document>: {passage}<|im_end|>\n<|im_start|>assistant\n`
  - Score candidates via logit probability of the `"yes"` token (or `LLAMA_POOLING_TYPE_RANK` if available in the model).
  - Implement `rerank(query: str, items: List[Dict[str, Any]], text_extractor: Callable, top_k: int) -> List[Dict[str, Any]]`:
    - Assign `rerank_score` to each item.
    - Sort items by `rerank_score` descending and return top `top_k`.
  - If disabled, unavailable, or upon exception, return items in original order.
- **Verification**:
  - Unit tests with mock Llama logits verifying score extraction, sorting, and fallback.
  - Fact verified: `fact-cross-encoder-reranker`, `fact-graceful-fallback`

### Step 6: Integrate Query Expansion & Re-ranking in `src/rubygemdb/services/hybrid_search.py`
- **Files**: `src/rubygemdb/services/hybrid_search.py`
- **Actions**:
  - In `hybrid_search`:
    - Compute dynamic candidate pool size: `pool_size = max(15, min(50, limit * settings.reranker_candidate_multiplier))`.
    - If `settings.query_expansion_enabled`:
      - Call `QueryExpander.expand(query)`.
      - Run keyword searches for the original query plus generated `lexical` tokens.
      - Run vector searches for the original query plus generated `vector` tokens.
      - Fuse all multi-pass hits with Reciprocal Rank Fusion into candidate dictionary.
    - If `settings.reranker_enabled` and fused candidates exist:
      - Rerank candidate gems against `query` using gem text representation (`"{name}: {description}"`).
      - Slice to requested `limit`.
- **Verification**:
  - Unit tests verifying multi-pass keyword/vector searches, candidate pool sizing, fused RRF scoring, and reranking order.
  - Fact verified: `fact-hybrid-search-pipeline`, `fact-candidate-pool-sizing`, `fact-graceful-fallback`

### Step 7: Integrate Query Expansion & Re-ranking in `src/rubygemdb/services/graphrag.py`
- **Files**: `src/rubygemdb/services/graphrag.py`
- **Actions**:
  - In `GraphRAGService.retrieve`:
    - Compute candidate pool size for vector hits: `candidate_k = max(15, min(50, top_k * settings.reranker_candidate_multiplier))`.
    - If `settings.query_expansion_enabled`:
      - Expand query and query txtai embeddings with vector expansions.
    - If `settings.reranker_enabled`:
      - Score candidate nodes against `query` using node representation (`"{name} {signature} {docstring}"`).
      - Select top `top_k` reranked nodes as `primary_node_ids`.
    - Proceed to 1-hop graph neighborhood expansion.
- **Verification**:
  - Unit tests verifying GraphRAG retrieve with expanded vector hits and reranked primary nodes.
  - Fact verified: `fact-graphrag-pipeline`, `fact-candidate-pool-sizing`, `fact-graceful-fallback`

### Step 8: Comprehensive Automated Test Suite
- **Files**: `tests/test_query_expansion_reranking.py`
- **Actions**:
  - Write test cases for:
    - Dependency import and config settings validation.
    - ModelManager caching and fallback on network/file failure.
    - QueryExpander prompt construction, token parsing, and degradation.
    - CrossEncoderReranker pair scoring, sorting, and degradation.
    - `hybrid_search` multi-pass RRF and reranking integration.
    - `GraphRAGService.retrieve` candidate pool sizing, primary node reranking, and neighborhood preservation.
  - Run the full project test suite to verify 0 regressions.
- **Verification**:
  - `uv run pytest tests/test_query_expansion_reranking.py`
  - `uv run pytest tests/`
  - Fact verified: `fact-automated-test-suite`

---

## Risks and Mitigation
1. **Model Download Latency on First Run**:
   - *Mitigation*: Downloads are strictly lazy and on-demand using `huggingface_hub.hf_hub_download`, with detailed logging. Default settings can keep `query_expansion_enabled` and `reranker_enabled` configurable via environment variables.
2. **CPU Inference Latency**:
   - *Mitigation*: Qwen3-Reranker 0.6B Q8_0 (~640MB) and 1.7B Q4_K_M (~1.1GB) are small and fast. Candidate pool is clamped to at most 50 items (default 15 for top_k=5), limiting reranking passes to milliseconds.
3. **Missing Hardware / Dependencies in Minimal Environments**:
   - *Mitigation*: All calls wrap in defensive try/except blocks falling back directly to baseline FTS/vector RRF search.
