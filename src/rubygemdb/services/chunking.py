"""Recursive Parent-Child Chunking Engine.

Provides hierarchical document splitting with dual parent modes ('paragraph' and 'full_doc'),
recursive delimiter cascades down to terminal character slicing, text preprocessing rules
with markdown code-block safeguards, and a preview inspection hook with exact character offsets.
"""

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ChunkConfig(BaseModel):
    """Configuration settings for recursive parent-child chunking."""

    parent_mode: Literal["paragraph", "full_doc"] = "paragraph"
    parent_max_chars: int = 4000
    child_max_chars: int = 512
    child_overlap_chars: int = 50
    delimiters: list[str] = Field(
        default_factory=lambda: ["\n\n", "\n", ". ", "? ", "! ", " ", ""]
    )
    normalize_whitespace: bool = True
    strip_urls_emails: bool = False

    @model_validator(mode="after")
    def validate_sizes(self) -> "ChunkConfig":
        if self.child_max_chars <= 0:
            raise ValueError("child_max_chars must be greater than 0")
        if self.parent_max_chars < self.child_max_chars:
            raise ValueError("parent_max_chars must be greater than or equal to child_max_chars")
        if self.child_overlap_chars < 0:
            raise ValueError("child_overlap_chars cannot be negative")
        if self.child_overlap_chars >= self.child_max_chars:
            raise ValueError("child_overlap_chars must be strictly less than child_max_chars")
        return self


class ChildChunk(BaseModel):
    """Fine-grained child chunk for embedding and vector similarity search."""

    chunk_id: str
    text: str
    start_offset: int
    end_offset: int
    char_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParentChunk(BaseModel):
    """Parent chunk containing broad context and nested child chunks."""

    parent_id: str
    text: str
    start_offset: int
    end_offset: int
    char_count: int
    children: list[ChildChunk] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkPreviewResult(BaseModel):
    """Hierarchical preview inspection output with aggregate statistics."""

    total_parents: int
    total_children: int
    avg_child_chars: float
    max_child_chars: int
    min_child_chars: int
    parent_chunks: list[ParentChunk] = Field(default_factory=list)


URL_RE = re.compile(r"https?://\S+|www\.\S+")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
FENCED_CODE_RE = re.compile(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)")


def preprocess_text(text: str, config: Optional[ChunkConfig] = None) -> str:
    """Preprocess input text: whitespace normalization & URL/email stripping with code-block bypass."""
    if not text:
        return ""
    cfg = config or ChunkConfig()
    if not cfg.normalize_whitespace and not cfg.strip_urls_emails:
        return text

    parts = FENCED_CODE_RE.split(text)
    processed_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Code block: preserve verbatim (bypass URL/email stripping and whitespace normalization)
            processed_parts.append(part)
        else:
            segment = part
            if cfg.strip_urls_emails:
                segment = URL_RE.sub("", segment)
                segment = EMAIL_RE.sub("", segment)
            if cfg.normalize_whitespace:
                lines = segment.split("\n")
                normalized_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
                segment = "\n".join(normalized_lines)
                segment = re.sub(r"\n{3,}", "\n\n", segment)
            processed_parts.append(segment)

    result = "".join(processed_parts)
    if cfg.normalize_whitespace:
        result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def _find_protected_code_blocks(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Identify character spans of fenced code blocks that fit within max_chars to treat atomically."""
    spans = []
    for m in FENCED_CODE_RE.finditer(text):
        if (m.end() - m.start()) <= max_chars:
            spans.append((m.start(), m.end()))
    return spans


def split_text_recursive(
    text: str,
    config: Optional[ChunkConfig] = None,
    parent_id: str = "p0",
) -> list[ChildChunk]:
    """Recursively split text through delimiter cascade down to child_max_chars with overlap."""
    if not text:
        return []
    cfg = config or ChunkConfig()
    if len(text) <= cfg.child_max_chars:
        return [
            ChildChunk(
                chunk_id=f"{parent_id}_c0",
                text=text,
                start_offset=0,
                end_offset=len(text),
                char_count=len(text),
            )
        ]

    protected_spans = _find_protected_code_blocks(text, cfg.child_max_chars)
    children: list[ChildChunk] = []
    curr_start = 0
    chunk_index = 0
    total_len = len(text)

    while curr_start < total_len:
        # If curr_start is strictly inside a protected code block, jump past it
        for p_s, p_e in protected_spans:
            if p_s < curr_start < p_e:
                curr_start = p_e
                break

        if curr_start >= total_len:
            break

        max_end = min(curr_start + cfg.child_max_chars, total_len)
        if max_end == total_len:
            curr_end = max_end
        else:
            inside_protected = None
            for p_s, p_e in protected_spans:
                if p_s < max_end < p_e:
                    inside_protected = (p_s, p_e)
                    break

            if inside_protected:
                p_s, p_e = inside_protected
                if p_s > curr_start:
                    curr_end = p_s
                else:
                    curr_end = min(p_e, max_end)
            else:
                found_split = None
                min_search_pos = curr_start + max(1, cfg.child_max_chars // 3)

                for delim in cfg.delimiters:
                    if delim == "":
                        # Terminal character split fallback
                        found_split = max_end
                        break

                    pos = text.rfind(delim, min_search_pos, max_end)
                    while pos != -1:
                        split_end = pos + len(delim)
                        if split_end <= max_end:
                            if not any(ps < pos < pe for ps, pe in protected_spans):
                                found_split = split_end
                                break
                        pos = text.rfind(delim, min_search_pos, pos)
                    if found_split is not None:
                        break

                curr_end = found_split if found_split is not None else max_end

        if curr_end <= curr_start:
            curr_end = min(curr_start + cfg.child_max_chars, total_len)

        chunk_text = text[curr_start:curr_end]
        children.append(
            ChildChunk(
                chunk_id=f"{parent_id}_c{chunk_index}",
                text=chunk_text,
                start_offset=curr_start,
                end_offset=curr_end,
                char_count=len(chunk_text),
            )
        )
        chunk_index += 1

        if curr_end >= total_len:
            break

        if cfg.child_overlap_chars <= 0:
            curr_start = curr_end
        else:
            raw_overlap = max(curr_start + 1, curr_end - cfg.child_overlap_chars)
            for p_s, p_e in protected_spans:
                if p_s < raw_overlap < p_e:
                    if p_s > curr_start:
                        raw_overlap = p_s
                    else:
                        raw_overlap = p_e
                    break

            if raw_overlap < curr_end:
                space_pos = text.find(" ", raw_overlap, curr_end)
                if space_pos != -1 and (curr_end - (space_pos + 1)) >= 5:
                    raw_overlap = space_pos + 1

            while raw_overlap < curr_end and text[raw_overlap] in (" ", "\t", "\n"):
                raw_overlap += 1

            curr_start = max(curr_start + 1, raw_overlap)

    return children


class RecursiveParentChildChunker:
    """Production-grade recursive parent-child chunker."""

    def __init__(self, config: Optional[ChunkConfig] = None):
        self.config = config or ChunkConfig()

    def _partition_parents(self, text: str, doc_id: str = "") -> list[ParentChunk]:
        """Partition preprocessed document into parent chunks up to parent_max_chars."""
        if not text:
            return []

        prefix = f"{doc_id}_" if doc_id else ""
        if self.config.parent_mode == "full_doc" or len(text) <= self.config.parent_max_chars:
            return [
                ParentChunk(
                    parent_id=f"{prefix}p0",
                    text=text,
                    start_offset=0,
                    end_offset=len(text),
                    char_count=len(text),
                )
            ]

        # Paragraph partition mode
        protected_spans = _find_protected_code_blocks(text, self.config.parent_max_chars)
        parents: list[ParentChunk] = []
        curr_start = 0
        p_idx = 0
        total_len = len(text)

        while curr_start < total_len:
            while curr_start < total_len and text[curr_start] in ("\n", " ", "\t"):
                curr_start += 1
            if curr_start >= total_len:
                break

            max_end = min(curr_start + self.config.parent_max_chars, total_len)
            if max_end == total_len:
                curr_end = max_end
            else:
                inside_protected = None
                for ps, pe in protected_spans:
                    if ps < max_end < pe:
                        inside_protected = (ps, pe)
                        break

                if inside_protected:
                    ps, pe = inside_protected
                    curr_end = ps if ps > curr_start else min(pe, max_end)
                else:
                    found_split = None
                    min_pos = curr_start + max(1, self.config.parent_max_chars // 3)
                    for delim in ["\n\n", "\n", ". ", " ", ""]:
                        if delim == "":
                            found_split = max_end
                            break
                        pos = text.rfind(delim, min_pos, max_end)
                        while pos != -1:
                            s_end = pos + len(delim)
                            if s_end <= max_end:
                                if not any(ps < pos < pe for ps, pe in protected_spans):
                                    found_split = s_end
                                    break
                            pos = text.rfind(delim, min_pos, pos)
                        if found_split is not None:
                            break
                    curr_end = found_split if found_split is not None else max_end

            if curr_end <= curr_start:
                curr_end = min(curr_start + self.config.parent_max_chars, total_len)

            p_text = text[curr_start:curr_end]
            parents.append(
                ParentChunk(
                    parent_id=f"{prefix}p{p_idx}",
                    text=p_text,
                    start_offset=curr_start,
                    end_offset=curr_end,
                    char_count=len(p_text),
                )
            )
            p_idx += 1
            curr_start = curr_end

        return parents

    def chunk_document(self, text: str, doc_id: str = "") -> list[ParentChunk]:
        """Preprocess document, partition into parent chunks, and recursively split into child chunks."""
        preprocessed = preprocess_text(text, self.config)
        parents = self._partition_parents(preprocessed, doc_id)
        for parent in parents:
            parent.children = split_text_recursive(
                parent.text,
                self.config,
                parent_id=parent.parent_id,
            )
        return parents

    def preview_chunks(self, text: str, doc_id: str = "") -> ChunkPreviewResult:
        """Produce hierarchical preview inspection with aggregate statistics."""
        parents = self.chunk_document(text, doc_id)
        total_parents = len(parents)
        all_children = [c for p in parents for c in p.children]
        total_children = len(all_children)
        if total_children == 0:
            return ChunkPreviewResult(
                total_parents=total_parents,
                total_children=0,
                avg_child_chars=0.0,
                max_child_chars=0,
                min_child_chars=0,
                parent_chunks=parents,
            )
        all_lens = [c.char_count for c in all_children]
        return ChunkPreviewResult(
            total_parents=total_parents,
            total_children=total_children,
            avg_child_chars=round(sum(all_lens) / total_children, 2),
            max_child_chars=max(all_lens),
            min_child_chars=min(all_lens),
            parent_chunks=parents,
        )


def preview_chunks(
    text: str,
    config: Optional[ChunkConfig] = None,
    doc_id: str = "",
) -> ChunkPreviewResult:
    """Convenience helper to preview chunks of a text with hierarchical statistics."""
    chunker = RecursiveParentChildChunker(config)
    return chunker.preview_chunks(text, doc_id=doc_id)
