"""Upload pipeline orchestrator.

Phase 5a: validate -> lock -> get-or-create -> [if exists, delete] ->
chunk -> embed -> payload -> upsert -> save Document -> release lock -> return.

Phase 5b: adds content_hash short-circuit and per-doc chunk cap.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from qdrant_client.models import PointStruct, SparseVector

from apps.core.timing import timer
from apps.documents.exceptions import (
    DocumentNotFoundError,
    DocumentTooLargeError,
    EmbedderError,
    NoEmbeddableContentError,
    QdrantWriteError,
)
from apps.documents.models import Document
from apps.ingestion.chunker import chunk_item
from apps.ingestion.embedder import (
    colbert_to_qdrant,
    embed_passages,
    sparse_to_qdrant,
)
from apps.ingestion.locks import upload_lock
from apps.ingestion.payload import ScrapedItem, ScrapedSource, build_payload
from apps.qdrant_core.client import get_qdrant_client
from apps.qdrant_core.collection import (
    delete_by_doc_id,
    get_or_create_collection,
)
from apps.qdrant_core.exceptions import QdrantError
from apps.qdrant_core.naming import collection_name as derive_collection_name
from apps.tenants.models import Bot, Tenant

logger = logging.getLogger(__name__)

MAX_CHUNKS_PER_DOC = 5000


@dataclass(frozen=True)
class UploadResult:
    doc_id: str
    chunks_created: int
    items_processed: int
    collection_name: str
    status: str


def _get_or_create_tenant(tenant_id: str) -> Tenant:
    try:
        tenant, _ = Tenant.objects.get_or_create(
            tenant_id=tenant_id,
            defaults={"name": tenant_id},
        )
        return tenant
    except IntegrityError:
        return Tenant.objects.get(tenant_id=tenant_id)


def _get_or_create_bot(tenant: Tenant, bot_id: str) -> Bot:
    try:
        bot, _ = Bot.objects.get_or_create(
            tenant=tenant,
            bot_id=bot_id,
            defaults={"name": bot_id},
        )
        return bot
    except IntegrityError:
        return Bot.objects.get(tenant=tenant, bot_id=bot_id)


def _compute_content_hash(items: list[dict]) -> str:
    h = hashlib.sha256()
    for item in items:
        h.update((item.get("content") or "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def _point_id_for_chunk(chunk_id: str) -> str:
    """Qdrant requires UUID or unsigned int as point id; chunk_id is a
    semantic string. Derive a stable UUID5 so re-uploads of the same
    chunk_id hit the same point. The chunk_id itself stays in payload.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_OID, chunk_id))


class UploadPipeline:
    @staticmethod
    def execute(
        *,
        tenant_id: str,
        bot_id: str,
        doc_id: str,
        body: dict,
    ) -> UploadResult:
        started = time.monotonic()
        with upload_lock(tenant_id, bot_id, doc_id):
            tenant = _get_or_create_tenant(tenant_id)
            bot = _get_or_create_bot(tenant, bot_id)

            existing = Document.objects.filter(doc_id=doc_id).first()
            is_replace = existing is not None
            if is_replace and (existing.tenant_id != tenant_id or existing.bot_id != bot_id):
                raise QdrantWriteError(
                    "doc_id collision across tenants/bots — refuse to overwrite.",
                    details={
                        "doc_id": doc_id,
                        "expected_tenant": tenant_id,
                        "found_tenant": existing.tenant_id,
                    },
                )

            incoming_hash = (body.get("content_hash") or "").strip()
            if not incoming_hash:
                incoming_hash = _compute_content_hash(body["items"])

            if existing and existing.chunk_count > 0 and existing.content_hash == incoming_hash:
                existing.save(update_fields=["last_refreshed_at"])
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info(
                    "upload_no_change",
                    extra={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "doc_id": doc_id,
                        "items_processed": existing.item_count,
                        "chunks_created": existing.chunk_count,
                        "status_code": 200,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                return UploadResult(
                    doc_id=doc_id,
                    chunks_created=existing.chunk_count,
                    items_processed=existing.item_count,
                    collection_name=derive_collection_name(tenant_id, bot_id),
                    status="no_change",
                )

            content_match = (
                Document.objects.filter(
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    content_hash=incoming_hash,
                    status=Document.ACTIVE,
                    chunk_count__gt=0,
                )
                .exclude(doc_id=doc_id)
                .first()
            )
            if content_match:
                content_match.save(update_fields=["last_refreshed_at"])
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info(
                    "upload_no_change_content_match",
                    extra={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "requested_doc_id": doc_id,
                        "existing_doc_id": str(content_match.doc_id),
                        "items_processed": content_match.item_count,
                        "chunks_created": content_match.chunk_count,
                        "status_code": 200,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                return UploadResult(
                    doc_id=str(content_match.doc_id),
                    chunks_created=content_match.chunk_count,
                    items_processed=content_match.item_count,
                    collection_name=derive_collection_name(tenant_id, bot_id),
                    status="no_change",
                )

            with timer("get_or_create"):
                try:
                    collection_name = get_or_create_collection(tenant_id, bot_id)
                except QdrantError as exc:
                    raise QdrantWriteError(
                        f"Collection get_or_create failed: {exc}",
                        details={"tenant_id": tenant_id, "bot_id": bot_id},
                    ) from exc

            items_data = body["items"]
            source_type = body["source_type"]
            flat: list[tuple[int, dict, object]] = []
            with timer("chunk"):
                for auto_idx, item_data in enumerate(items_data):
                    chunks = chunk_item(
                        item_data["content"],
                        source_type=source_type,
                        item_index=auto_idx,
                    )
                    for c in chunks:
                        flat.append((auto_idx, item_data, c))

            if not flat:
                raise NoEmbeddableContentError(
                    "No chunks survived after chunking.",
                    details={"items_count": len(items_data)},
                )

            if len(flat) > MAX_CHUNKS_PER_DOC:
                raise DocumentTooLargeError(
                    f"Document produces {len(flat)} chunks, max is {MAX_CHUNKS_PER_DOC}",
                    details={"chunk_count": len(flat), "max": MAX_CHUNKS_PER_DOC},
                )

            with timer("embed"):
                try:
                    texts = [c.text for _, _, c in flat]
                    embeddings = embed_passages(texts)
                except Exception as exc:
                    raise EmbedderError(
                        f"Embedder failed: {exc}",
                        details={"chunk_count": len(flat)},
                    ) from exc

            source = ScrapedSource(
                type=source_type,
                filename=body.get("source_filename"),
                url=body.get("source_url"),
                content_hash=incoming_hash,
            )

            points: list[PointStruct] = []
            for i, (auto_idx, item_data, chunk) in enumerate(flat):
                item = ScrapedItem(
                    item_index=auto_idx,
                    section_path=item_data.get("section_path") or [],
                    page_number=item_data.get("page_number"),
                )
                payload_dict = build_payload(
                    chunk,
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    doc_id=doc_id,
                    item=item,
                    source=source,
                )
                sparse_qd = sparse_to_qdrant(embeddings["sparse"][i])
                dense_vec = embeddings["dense"][i]
                dense_list = dense_vec.tolist() if hasattr(dense_vec, "tolist") else list(dense_vec)
                points.append(
                    PointStruct(
                        id=_point_id_for_chunk(payload_dict["chunk_id"]),
                        vector={
                            "dense": dense_list,
                            "bm25": SparseVector(
                                indices=sparse_qd["indices"],
                                values=sparse_qd["values"],
                            ),
                            "colbert": colbert_to_qdrant(embeddings["colbert"][i]),
                        },
                        payload=payload_dict,
                    )
                )

            if is_replace:
                try:
                    delete_by_doc_id(tenant_id, bot_id, doc_id)
                except QdrantError as exc:
                    raise QdrantWriteError(
                        f"delete_by_doc_id failed during replace: {exc}",
                        details={"doc_id": doc_id},
                    ) from exc

            with timer("upsert"):
                try:
                    client = get_qdrant_client()
                    client.upsert(collection_name=collection_name, points=points)
                except Exception as exc:
                    raise QdrantWriteError(
                        f"upsert failed: {exc}",
                        details={"doc_id": doc_id, "chunks": len(points)},
                    ) from exc

            with timer("doc_save"), transaction.atomic():
                Document.objects.update_or_create(
                    doc_id=doc_id,
                    defaults={
                        "bot_ref": bot,
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "source_type": source_type,
                        "source_filename": body.get("source_filename"),
                        "source_url": body.get("source_url"),
                        "content_hash": incoming_hash,
                        "chunk_count": len(points),
                        "item_count": len(items_data),
                        "status": Document.ACTIVE,
                        "error_message": None,
                        "raw_payload": body,
                    },
                )

            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "upload_succeeded",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": doc_id,
                    "items_processed": len(items_data),
                    "chunks_created": len(points),
                    "is_replace": is_replace,
                    "elapsed_ms": elapsed_ms,
                },
            )

            return UploadResult(
                doc_id=doc_id,
                chunks_created=len(points),
                items_processed=len(items_data),
                collection_name=collection_name,
                status="replaced" if is_replace else "created",
            )


@dataclass(frozen=True)
class DeleteResult:
    doc_id: str
    chunks_deleted: int
    was_already_deleted: bool


class DeletePipeline:
    @staticmethod
    def execute(
        *,
        tenant_id: str,
        bot_id: str,
        doc_id: str,
    ) -> DeleteResult:
        started = time.monotonic()
        with upload_lock(tenant_id, bot_id, doc_id):
            existing = Document.objects.filter(doc_id=doc_id).first()
            if not existing or (existing.tenant_id != tenant_id or existing.bot_id != bot_id):
                raise DocumentNotFoundError(
                    f"Document {doc_id} not found.",
                    details={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "doc_id": doc_id,
                    },
                )

            was_already_deleted = existing.status == Document.DELETED

            try:
                chunks_deleted = delete_by_doc_id(tenant_id, bot_id, doc_id)
            except QdrantError as exc:
                raise QdrantWriteError(
                    f"delete_by_doc_id failed: {exc}",
                    details={"doc_id": doc_id},
                ) from exc

            existing.status = Document.DELETED
            existing.chunk_count = 0
            existing.error_message = None
            existing.save(
                update_fields=[
                    "status",
                    "chunk_count",
                    "error_message",
                    "last_refreshed_at",
                ]
            )

            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "delete_succeeded",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": doc_id,
                    "chunks_deleted": chunks_deleted,
                    "was_already_deleted": was_already_deleted,
                    "elapsed_ms": elapsed_ms,
                },
            )

            return DeleteResult(
                doc_id=doc_id,
                chunks_deleted=chunks_deleted,
                was_already_deleted=was_already_deleted,
            )
