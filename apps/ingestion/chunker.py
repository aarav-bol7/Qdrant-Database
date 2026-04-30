"""Per-source-type chunker.

Wraps langchain-text-splitters' RecursiveCharacterTextSplitter with
sizing locked per source type. Tokens counted via BGE-M3's tokenizer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from apps.ingestion.embedder import count_tokens

logger = logging.getLogger(__name__)


CHUNK_CONFIG: dict[str, dict[str, float]] = {
    "pdf": {"size": 500, "overlap_pct": 0.15},
    "docx": {"size": 500, "overlap_pct": 0.15},
    "url": {"size": 400, "overlap_pct": 0.10},
    "html": {"size": 400, "overlap_pct": 0.10},
    "csv": {"size": 200, "overlap_pct": 0.10},
    "faq": {"size": 200, "overlap_pct": 0.10},
    "image": {"size": 300, "overlap_pct": 0.10},
    "text": {"size": 200, "overlap_pct": 0.15},
}
DEFAULT_CHUNK_CONFIG: dict[str, float] = {"size": 400, "overlap_pct": 0.10}
MIN_CHUNK_CHARS = 50
MAX_CHUNK_TOKENS = 600

_CHARS_PER_TOKEN = 4

_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", ", ", " ", ""]


@dataclass(frozen=True)
class Chunk:
    text: str
    chunk_index: int
    char_count: int
    token_count: int


def chunk_item(
    content: str,
    *,
    source_type: str,
    item_index: int,
) -> list[Chunk]:
    """Split one item.content into chunks per the locked per-source-type config.

    Returns chunks fully contained within `content` (never crosses item
    boundaries). Empty/whitespace-only `content` returns []. Very short
    content (less than MIN_CHUNK_CHARS) returns a single chunk = the
    content itself, NOT [].
    """
    if not content or not content.strip():
        return []

    config = CHUNK_CONFIG.get(source_type, DEFAULT_CHUNK_CONFIG)
    if source_type not in CHUNK_CONFIG:
        logger.warning(
            "chunker_unknown_source_type",
            extra={"source_type": source_type, "fallback": "default"},
        )

    target_tokens = int(config["size"])
    overlap_tokens = int(target_tokens * config["overlap_pct"])

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=target_tokens * _CHARS_PER_TOKEN,
        chunk_overlap=overlap_tokens * _CHARS_PER_TOKEN,
        length_function=len,
        separators=_SEPARATORS,
    )
    raw_chunks = [c.strip() for c in splitter.split_text(content) if c.strip()]

    chunks: list[Chunk] = []
    for raw_text in raw_chunks:
        text = _truncate_to_max_tokens(raw_text)
        if not text:
            continue

        token_n = count_tokens(text)

        if len(text) < MIN_CHUNK_CHARS and chunks:
            prev = chunks[-1]
            merged = f"{prev.text} {text}"
            merged_token_n = count_tokens(merged)
            if merged_token_n <= MAX_CHUNK_TOKENS:
                chunks[-1] = Chunk(
                    text=merged,
                    chunk_index=prev.chunk_index,
                    char_count=len(merged),
                    token_count=merged_token_n,
                )
                continue

        chunks.append(
            Chunk(
                text=text,
                chunk_index=len(chunks),
                char_count=len(text),
                token_count=token_n,
            )
        )

    if not chunks and content.strip():
        text = _truncate_to_max_tokens(content.strip())
        chunks.append(
            Chunk(
                text=text,
                chunk_index=0,
                char_count=len(text),
                token_count=count_tokens(text),
            )
        )

    logger.debug(
        "chunker_done",
        extra={
            "source_type": source_type,
            "item_index": item_index,
            "input_chars": len(content),
            "n_chunks": len(chunks),
        },
    )
    return chunks


def _truncate_to_max_tokens(text: str) -> str:
    """Hard-cap text at MAX_CHUNK_TOKENS via a binary char-count search."""
    if not text:
        return text
    n = count_tokens(text)
    if n <= MAX_CHUNK_TOKENS:
        return text
    target_chars = int(len(text) * MAX_CHUNK_TOKENS / n)
    truncated = text[:target_chars].rstrip()
    while count_tokens(truncated) > MAX_CHUNK_TOKENS and truncated:
        truncated = truncated[: int(len(truncated) * 0.95)].rstrip()
    return truncated
