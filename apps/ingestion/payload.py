"""Qdrant payload builder for chunks.

Phase 7.5 trims the payload to 17 keys (was 20). Removed: section_title,
category, tags. Removed kwarg: custom_metadata.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from apps.ingestion.chunker import Chunk


@dataclass(frozen=True)
class ScrapedSource:
    type: str
    filename: str | None = None
    url: str | None = None
    content_hash: str = ""


@dataclass(frozen=True)
class ScrapedItem:
    item_index: int
    section_path: list[str] = field(default_factory=list)
    page_number: int | None = None


def build_chunk_id(doc_id: str, item_index: int, chunk_index: int) -> str:
    return f"{doc_id}__i{item_index}__c{chunk_index}"


def build_payload(
    chunk: Chunk,
    *,
    tenant_id: str,
    bot_id: str,
    doc_id: str,
    item: ScrapedItem,
    source: ScrapedSource,
    uploaded_at: datetime.datetime | None = None,
) -> dict[str, Any]:
    now = uploaded_at or datetime.datetime.now(datetime.UTC)

    return {
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "doc_id": doc_id,
        "chunk_id": build_chunk_id(doc_id, item.item_index, chunk.chunk_index),
        "version": 1,
        "is_active": True,
        "uploaded_at": now.isoformat(),
        "source_type": source.type,
        "source_filename": source.filename,
        "source_url": source.url,
        "source_item_index": item.item_index,
        "source_content_hash": source.content_hash,
        "section_path": list(item.section_path),
        "page_number": item.page_number,
        "text": chunk.text,
        "char_count": chunk.char_count,
        "token_count": chunk.token_count,
    }
