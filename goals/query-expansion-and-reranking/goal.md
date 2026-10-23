# Goal: Query Expansion and Cross-Encoder Re-ranking

## Articulated Goal
Implement local GGUF-based query expansion and cross-encoder re-ranking in RubyGemDB to enhance retrieval recall and precision across both the gem catalog (`hybrid_search`) and ephemeral code exploration (`GraphRAGService`). User queries are expanded into lexical and vector variations using `mradermacher/qmd-query-expansion-1.7B-GGUF`, multi-pass results are fused via Reciprocal Rank Fusion, and candidates are re-scored using `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` via `llama_cpp`.

## Shared Understanding
- [facts.md](facts.md): The accepted, testable facts defining deliverables, configuration, candidate pool sizing, graceful degradation, and verification requirements.

## Execution Plan
- [plan.md](plan.md): The approved 8-step implementation plan covering dependency declaration, configuration settings, GGUF model management, query expansion service, cross-encoder reranker, search/GraphRAG integration, and automated tests.

## Done Condition
1. `llama-cpp-python` and `huggingface_hub` are present in `pyproject.toml` and locked.
2. `Settings` in `src/rubygemdb/core/config.py` provides expander and reranker configurations with environment variable overrides.
3. `GGUFModelManager`, `QueryExpander`, and `CrossEncoderReranker` services are implemented with lazy loading, automatic HF hub caching, and graceful fallback.
4. `hybrid_search` and `GraphRAGService.retrieve` execute multi-pass expansion, RRF candidate pooling, and cross-encoder re-ranking.
5. All new and existing automated tests pass (`uv run pytest tests/`).
