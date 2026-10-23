# Implementation Plan: Recursive Parent-Child Chunking

## Solution Approach
Implement a production-grade, configurable **Recursive Parent-Child Chunking** service in `rubygemdb.services.chunking` and integrate it across RubyGemDB's vector indexing pipelines (`TxtaiAgent` and `GraphRAGService`). The solution provides:
1. **Configurable Sizing & Dual Parent Mode**: Pydantic-backed `ChunkConfig` supporting both `paragraph` mode (hierarchical parent partitions up to `parent_max_chars`) and `full_doc` mode (entire document as parent), cascading recursively through delimiters down to `child_max_chars` with configurable character overlap.
2. **Text Preprocessing with Code-Block Safeguards**: Whitespace/newline normalization and optional URL/email stripping that automatically bypasses fenced code blocks and GraphRAG code examples.
3. **Clean Preview Hook**: A `preview_chunks()` hook returning a hierarchical `ChunkPreviewResult` with summary statistics and nested `ParentChunk` -> `ChildChunk` trees with exact character offsets (`parent_text[start:end] == child_text`), ready for future TUI or MCP consumers.
4. **Parent-Resolved & Deduplicated Retrieval**: Updating `TxtaiAgent` and `GraphRAGService` so vector search queries fine-grained child chunks, resolves hits to their parent contexts (deduplicated by gem or parent AST node ID), and recalls full parent text without polluting results with duplicate gem entries.

---

## Ordered Steps & Files Touched

### Step 1: Configuration Settings in `core/config.py`
- **Files**: `src/rubygemdb/core/config.py`
- **Changes**: Add chunking settings to `Settings`:
  - `chunk_parent_mode: str = "paragraph"`
  - `chunk_parent_max_chars: int = 4000`
  - `chunk_child_max_chars: int = 512`
  - `chunk_child_overlap_chars: int = 50`
  - `chunk_normalize_whitespace: bool = True`
  - `chunk_strip_urls_emails: bool = False`
- **Verification**: `python -c "from rubygemdb.core.config import settings; assert settings.chunk_child_max_chars == 512"`

### Step 2: Core Chunking Engine & Models in `services/chunking.py`
- **Files**: `src/rubygemdb/services/chunking.py` (new)
- **Changes**:
  - Define Pydantic models: `ChunkConfig`, `ChildChunk`, `ParentChunk`, `ChunkPreviewResult`.
  - Implement `preprocess_text()` with whitespace normalization, regex sanitization for URLs and emails, and fenced code block (` ```...``` `) detection/safeguard.
  - Implement `split_text_recursive()`: cascade through delimiters `["\n\n", "\n", ". ", "? ", "! ", " ", ""]` down to terminal character splits so unbroken strings never exceed `child_max_chars`.
  - Implement `RecursiveParentChildChunker`:
    - `chunk_document()` supporting both `paragraph` and `full_doc` modes with overlap.
    - `preview_chunks()` returning `ChunkPreviewResult` with verified character offsets.
- **Verification**: Unit tests in Step 5 verifying delimiter recursion, terminal splitting, and preview offsets.

### Step 3: Integrate with `TxtaiAgent` (Inventory & Dynamic Memory)
- **Files**: `src/rubygemdb/agent.py`
- **Changes**:
  - Replace legacy `create_child_chunks()` helper with `RecursiveParentChildChunker`.
  - Update `TxtaiAgent._initialize_index`: chunk parent descriptions into child documents with `parent_text`, `parent_id`, and `child_id`.
  - Update `TxtaiAgent.index_memory`: chunk dynamic memories into linked parent/child documents.
  - Update `TxtaiAgent._vector_search`: query child chunk embeddings in `txtai`, deduplicate hits by parent gem `name` to the highest-scoring child match, and return parent context.
- **Verification**: `uv run pytest tests/test_hybrid_search.py -v`

### Step 4: Integrate with `GraphRAGService`
- **Files**: `src/rubygemdb/services/graphrag.py`
- **Changes**:
  - In `GraphRAGService._build_vector_index`: apply `RecursiveParentChildChunker` to long docstrings and code examples (`type in ("method", "class", "module", "example")`) when exceeding `child_max_chars`. Tag child documents with `parent_node_id = nid`.
  - In `GraphRAGService.retrieve`: when vector hits return child document IDs or `parent_node_id`, resolve back to the parent AST `node_id` in `self.nodes_by_id` before executing 1-hop graph neighborhood expansion.
- **Verification**: `uv run pytest tests/test_graphrag.py tests/test_agent_graphrag.py -v`

### Step 5: Comprehensive Automated Test Suite
- **Files**: `tests/test_chunking.py` (new)
- **Changes**:
  - Test `ChunkConfig` defaults and validation.
  - Test preprocessing rules (whitespace collapsing, URL/email stripping, code-block bypass).
  - Test `paragraph` mode vs `full_doc` mode.
  - Test recursive delimiter cascade and terminal hard-character fallback on unbroken tokens.
  - Test child overlap continuity and bounded lengths.
  - Test fenced code block preservation.
  - Test `preview_chunks` hierarchical output, summary stats, and exact offset coordinates (`parent_text[start:end] == child_text`).
  - Test vector search deduplication and parent resolution in `TxtaiAgent` and `GraphRAGService`.
- **Verification**: `uv run pytest tests/test_chunking.py -v`

### Step 6: Linting & Type Checking
- **Files**: All modified files
- **Verification**:
  - `uv run ruff check src/rubygemdb/services/chunking.py src/rubygemdb/agent.py src/rubygemdb/services/graphrag.py src/rubygemdb/core/config.py tests/test_chunking.py`
  - `uv run mypy src/rubygemdb/services/chunking.py src/rubygemdb/core/config.py`

---

## Risks & Open Questions
- **txtai Schema Compatibility**: Ensure existing cached txtai vector indexes don't crash when loaded with documents that have new `parent_id` or `child_id` fields. txtai's content backend accepts arbitrary dictionary keys, which is already used by `parent_text`.
- **Reindexing Consideration**: Existing txtai indexes built prior to recursive parent-child chunking will continue to work, but running `rubygemdb --embed` or `reclassify --embed` will rebuild the index under the new recursive parent-child chunking.
