"""POST /v1/tenants/<tenant_id>/bots/<bot_id>/documents.
DELETE /v1/tenants/<tenant_id>/bots/<bot_id>/documents/<uuid:doc_id>.
"""

from __future__ import annotations

import logging
import time
import uuid

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.middleware import set_request_context
from apps.documents.exceptions import ConcurrentUploadError, UploadError
from apps.documents.serializers import SearchRequestSerializer, UploadBodySerializer
from apps.ingestion.pipeline import DeletePipeline, UploadPipeline, UploadResult
from apps.tenants.validators import InvalidIdentifierError, normalize_slug, validate_slug

logger = logging.getLogger(__name__)


class UploadDocumentView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request: Request, tenant_id: str, bot_id: str) -> Response:
        started = time.monotonic()
        doc_id_str: str | None = None
        try:
            try:
                tenant_id = normalize_slug(tenant_id)
                bot_id = normalize_slug(bot_id)
                validate_slug(tenant_id, field_name="tenant_id")
                validate_slug(bot_id, field_name="bot_id")
            except InvalidIdentifierError as exc:
                logger.info(
                    "upload_rejected_invalid_slug",
                    extra={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "status_code": 400,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return _error_response(
                    http_status=400,
                    code="invalid_slug",
                    message=str(exc),
                )

            set_request_context(tenant_id=tenant_id, bot_id=bot_id)

            serializer = UploadBodySerializer(data=request.data)
            if not serializer.is_valid():
                logger.info(
                    "upload_rejected_invalid_payload",
                    extra={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "status_code": 400,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return _error_response(
                    http_status=400,
                    code="invalid_payload",
                    message="Body validation failed.",
                    details=serializer.errors,
                )
            body = serializer.validated_data

            doc_id = body.get("doc_id") or uuid.uuid4()
            doc_id_str = str(doc_id)
            set_request_context(doc_id=doc_id_str)

            try:
                result: UploadResult = UploadPipeline.execute(
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    doc_id=doc_id_str,
                    body=body,
                )
            except UploadError as exc:
                logger.error(
                    "upload_failed",
                    extra={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "doc_id": doc_id_str,
                        "code": exc.code,
                        "status_code": exc.http_status,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    },
                    exc_info=True,
                )
                response = _error_response(
                    http_status=exc.http_status,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                )
                if isinstance(exc, ConcurrentUploadError):
                    response["Retry-After"] = str(exc.retry_after)
                return response

            logger.info(
                "upload_succeeded_response",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": result.doc_id,
                    "items_processed": result.items_processed,
                    "chunks_created": result.chunks_created,
                    "status": result.status,
                    "status_code": 200 if result.status == "no_change" else 201,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
            )
            status_code = 200 if result.status == "no_change" else 201
            return Response(
                {
                    "doc_id": result.doc_id,
                    "chunks_created": result.chunks_created,
                    "items_processed": result.items_processed,
                    "collection_name": result.collection_name,
                    "status": result.status,
                },
                status=status_code,
            )
        except Exception as exc:
            logger.error(
                "upload_unhandled_exception",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": doc_id_str,
                    "status_code": 500,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
                exc_info=True,
            )
            return _error_response(
                http_status=500,
                code="internal_error",
                message=f"{type(exc).__name__}: {exc}",
            )


class DeleteDocumentView(APIView):
    permission_classes = [permissions.AllowAny]

    def delete(self, request: Request, tenant_id: str, bot_id: str, doc_id) -> Response:
        started = time.monotonic()
        doc_id_str = str(doc_id)
        try:
            try:
                tenant_id = normalize_slug(tenant_id)
                bot_id = normalize_slug(bot_id)
                validate_slug(tenant_id, field_name="tenant_id")
                validate_slug(bot_id, field_name="bot_id")
            except InvalidIdentifierError as exc:
                logger.info(
                    "delete_rejected_invalid_slug",
                    extra={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "doc_id": doc_id_str,
                        "status_code": 400,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return _error_response(
                    http_status=400,
                    code="invalid_slug",
                    message=str(exc),
                )

            set_request_context(tenant_id=tenant_id, bot_id=bot_id, doc_id=doc_id_str)

            try:
                result = DeletePipeline.execute(
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    doc_id=doc_id_str,
                )
            except UploadError as exc:
                logger.error(
                    "delete_failed",
                    extra={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "doc_id": doc_id_str,
                        "code": exc.code,
                        "status_code": exc.http_status,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    },
                    exc_info=True,
                )
                response = _error_response(
                    http_status=exc.http_status,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                )
                if isinstance(exc, ConcurrentUploadError):
                    response["Retry-After"] = str(exc.retry_after)
                return response

            logger.info(
                "delete_succeeded_response",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": result.doc_id,
                    "chunks_deleted": result.chunks_deleted,
                    "was_already_deleted": result.was_already_deleted,
                    "status_code": 204,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
            )
            return Response(status=204)
        except Exception as exc:
            logger.error(
                "delete_unhandled_exception",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": doc_id_str,
                    "status_code": 500,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                },
                exc_info=True,
            )
            return _error_response(
                http_status=500,
                code="internal_error",
                message=f"{type(exc).__name__}: {exc}",
            )


class SearchDocumentsView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request: Request, tenant_id: str, bot_id: str) -> Response:
        started = time.monotonic()
        try:
            try:
                tenant_id = normalize_slug(tenant_id)
                bot_id = normalize_slug(bot_id)
                validate_slug(tenant_id, field_name="tenant_id")
                validate_slug(bot_id, field_name="bot_id")
            except InvalidIdentifierError as exc:
                return _error_response(http_status=400, code="invalid_slug", message=str(exc))

            set_request_context(tenant_id=tenant_id, bot_id=bot_id)

            serializer = SearchRequestSerializer(data=request.data)
            if not serializer.is_valid():
                return _error_response(
                    http_status=400,
                    code="invalid_payload",
                    message="Body validation failed.",
                    details=serializer.errors,
                )
            body = serializer.validated_data
            filters = body.get("filters") or {}

            from apps.qdrant_core.exceptions import QdrantConnectionError
            from apps.qdrant_core.search import CollectionNotFoundError, search

            try:
                result = search(
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    query=body["query"].strip(),
                    top_k=body.get("top_k", 5),
                    source_types=list(filters.get("source_types") or []) or None,
                    tags=list(filters.get("tags") or []) or None,
                    category=(filters.get("category") or None),
                )
            except CollectionNotFoundError:
                return _error_response(
                    http_status=404,
                    code="collection_not_found",
                    message="No collection for this bot. Upload a document first.",
                )
            except QdrantConnectionError as exc:
                logger.warning("http_search_qdrant_unavailable", extra={"error": str(exc)})
                return _error_response(
                    http_status=503,
                    code="qdrant_unavailable",
                    message=str(exc),
                )
            except Exception as exc:
                logger.error("http_search_failed", exc_info=True)
                return _error_response(
                    http_status=500,
                    code="internal_error",
                    message=f"{type(exc).__name__}: {exc}",
                )

            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "http_search_succeeded",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "query_length": len(body["query"]),
                    "top_k_requested": body.get("top_k", 5),
                    "results_returned": len(result["chunks"]),
                    "total_candidates": result["total_candidates"],
                    "threshold_used": result["threshold_used"],
                    "elapsed_ms": elapsed_ms,
                },
            )
            return Response(result, status=200)
        except Exception as exc:
            logger.error("http_search_unhandled_exception", exc_info=True)
            return _error_response(
                http_status=500,
                code="internal_error",
                message=f"{type(exc).__name__}: {exc}",
            )


def _error_response(
    *,
    http_status: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> Response:
    body: dict = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return Response(body, status=http_status)
