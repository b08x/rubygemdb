# Facts

- llama-cpp-python and huggingface_hub are declared in pyproject.toml dependencies and lockable via uv.
- Settings in core/config.py includes configurable fields and environment variable aliases for expander and reranker repository IDs, GGUF filenames, enable/disable toggles, context sizes, and candidate pool multipliers.
- A dedicated GGUF model manager handles on-demand lazy downloading and caching of weights from Hugging Face Hub using huggingface_hub.hf_hub_download.
- A QueryExpander service uses llama_cpp with mradermacher/qmd-query-expansion-1.7B-GGUF (default Q4_K_M) to generate semantic query variations and search keywords.
- A CrossEncoderReranker service uses llama_cpp with ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF to score and re-order candidate passages against the query.
- hybrid_search executes multi-pass retrieval with original and expanded queries, fuses candidates with Reciprocal Rank Fusion (RRF), and applies cross-encoder re-ranking to top candidate gems.
- GraphRAGService.retrieve expands the initial query, retrieves an oversampled candidate pool of symbol and documentation chunks, and applies cross-encoder re-ranking before 1-hop graph neighborhood expansion.
- Candidate retrieval before re-ranking dynamically scales to 3x the requested top_k (clamped between 15 and 50) before cross-encoder truncation.
- When query expansion or re-ranking models are disabled, missing, or encounter runtime errors, search seamlessly falls back to baseline retrieval without raising exceptions.
- Automated tests cover configuration parsing, query expansion generation, cross-encoder scoring, multi-pass RRF fusion, and fallback behavior when models are absent or mocked.
