# Goal: Recursive Parent-Child Chunking

## Articulated Goal

Replace the primitive paragraph/sentence chunker in RubyGemDB with a configurable, production-grade **Recursive Parent-Child Chunking** engine (`services/chunking.py`). The engine supports dual parent modes (`paragraph` partition mode vs. `full_doc` mode), recursive delimiter cascades (`\n\n` -> `\n` -> sentence boundaries -> words -> terminal characters) down to `child_max_chars` with configurable overlap, text preprocessing rules with code-block safeguards, and a clean hierarchical preview inspection hook (`preview_chunks`). It integrates seamlessly into `TxtaiAgent` (inventory and dynamic memory indexing) and `GraphRAGService` (AST docstrings and code examples) with parent-resolved, best-child deduplicated vector retrieval.

## Shared Understanding

See [facts.md](facts.md) — 15 accepted facts covering:
1. `ChunkConfig` Pydantic model and defaults.
2. Text pre-processing whitespace normalization.
3. Pre-processing URL/email stripping with automatic code-block bypass.
4. `paragraph` parent mode partitioning.
5. `full_doc` parent mode.
6. Recursive delimiter cascade and terminal character splitting.
7. Configurable child chunk overlap.
8. Markdown fenced code block boundary preservation.
9. Hierarchical `preview_chunks` inspection hook with exact character offsets (`parent_text[start:end] == child_text`).
10. `TxtaiAgent` inventory and memory parent-child indexing.
11. `TxtaiAgent` vector search deduplication by parent gem.
12. `GraphRAGService` AST docstrings and code example chunking.
13. `GraphRAGService` child-to-parent node resolution and 1-hop graph neighborhood expansion.
14. Application settings in `core/config.py`.
15. Comprehensive automated test suite in `tests/test_chunking.py`.

## Execution Plan

See [plan.md](plan.md) — 6 ordered steps:
1. Configuration settings in `src/rubygemdb/core/config.py`
2. Core chunking engine & data models in `src/rubygemdb/services/chunking.py`
3. Integration with `TxtaiAgent` (inventory & dynamic memory) in `src/rubygemdb/agent.py`
4. Integration with `GraphRAGService` in `src/rubygemdb/services/graphrag.py`
5. Comprehensive automated test suite in `tests/test_chunking.py`
6. Linting, type checking, and test verification across the workspace

## Done Condition

All 15 facts hold on the real codebase:
- `src/rubygemdb/services/chunking.py` provides `RecursiveParentChildChunker`, `ChunkConfig`, `ParentChunk`, `ChildChunk`, `ChunkPreviewResult`, and `preview_chunks()`.
- Legacy `create_child_chunks()` helper in `src/rubygemdb/agent.py` is upgraded to the new chunker.
- `TxtaiAgent._vector_search` executes vector search over child chunks, deduplicates hits to the best-scoring child per gem, and returns the full parent context.
- `GraphRAGService` chunks long symbols/docstrings/examples, linking child chunks to parent node IDs, and resolves hits to parent nodes for graph expansion.
- `uv run pytest tests/test_chunking.py tests/test_hybrid_search.py tests/test_graphrag.py tests/test_agent_graphrag.py` passes with 0 failures.
- `uv run ruff check` and `uv run mypy` pass cleanly.
