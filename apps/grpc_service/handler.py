"""SearchService gRPC handler."""

from __future__ import annotations

import logging
import time
from functools import wraps

import grpc

from apps.core.metrics_recorders import record_grpc_request
from apps.grpc_service.generated import search_pb2, search_pb2_grpc
from apps.qdrant_core.exceptions import QdrantConnectionError, QdrantError
from apps.qdrant_core.search import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    CollectionNotFoundError,
    search,
)
from apps.tenants.validators import InvalidIdentifierError, validate_slug

logger = logging.getLogger(__name__)

VERSION = "0.1.0-dev"


def _record_metrics(rpc_name: str):
    """Decorator that records gRPC counters + latency around an RPC method."""

    def deco(fn):
        @wraps(fn)
        def wrapper(self, request, context):
            started = time.monotonic()
            status_code: grpc.StatusCode = grpc.StatusCode.OK
            try:
                result = fn(self, request, context)
                code = context.code()
                status_code = code if code is not None else grpc.StatusCode.OK
                return result
            except grpc.RpcError as exc:
                code = getattr(exc, "code", lambda: grpc.StatusCode.UNKNOWN)()
                status_code = code if code is not None else grpc.StatusCode.UNKNOWN
                raise
            except Exception:
                status_code = grpc.StatusCode.INTERNAL
                raise
            finally:
                try:
                    record_grpc_request(
                        rpc=rpc_name,
                        status_code=getattr(status_code, "name", str(status_code)),
                        duration_seconds=time.monotonic() - started,
                    )
                except Exception:
                    logger.exception("grpc_metrics_record_failed", extra={"rpc": rpc_name})

        return wrapper

    return deco


class VectorSearchService(search_pb2_grpc.VectorSearchServicer):
    @_record_metrics("Search")
    def Search(self, request, context):  # noqa: N802
        started = time.monotonic()

        try:
            validate_slug(request.tenant_id, field_name="tenant_id")
            validate_slug(request.bot_id, field_name="bot_id")
        except InvalidIdentifierError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

        query = (request.query or "").strip()
        if not query:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Query must be non-empty.")

        top_k = request.top_k or DEFAULT_TOP_K
        if top_k < 1 or top_k > MAX_TOP_K:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"top_k must be in [1, {MAX_TOP_K}], got {top_k}.",
            )

        if not request.filters.only_active:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "filters.only_active must be true in v1.",
            )

        source_types = list(request.filters.source_types) or None
        tags = list(request.filters.tags) or None
        category = request.filters.category or None

        try:
            result = search(
                tenant_id=request.tenant_id,
                bot_id=request.bot_id,
                query=query,
                top_k=top_k,
                source_types=source_types,
                tags=tags,
                category=category,
            )
        except CollectionNotFoundError:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                "Collection does not exist for this tenant/bot.",
            )
        except QdrantConnectionError as exc:
            logger.warning("search_qdrant_unavailable", extra={"error": str(exc)})
            context.abort(grpc.StatusCode.UNAVAILABLE, f"Qdrant unavailable: {exc}")
        except QdrantError as exc:
            logger.error("search_qdrant_error", extra={"error": str(exc)}, exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, f"Qdrant error: {exc}")
        except Exception as exc:
            logger.error("search_unexpected_error", extra={"error": str(exc)}, exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, f"Unexpected error: {exc}")

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "search_succeeded",
            extra={
                "tenant_id": request.tenant_id,
                "bot_id": request.bot_id,
                "query_length": len(query),
                "top_k_requested": top_k,
                "results_returned": len(result["chunks"]),
                "total_candidates": result["total_candidates"],
                "threshold_used": result["threshold_used"],
                "elapsed_ms": elapsed_ms,
            },
        )

        response = search_pb2.SearchResponse(
            total_candidates=result["total_candidates"],
            threshold_used=result["threshold_used"],
        )
        for chunk_dict in result["chunks"]:
            chunk_msg = search_pb2.Chunk(
                chunk_id=chunk_dict.get("chunk_id", ""),
                doc_id=chunk_dict.get("doc_id", ""),
                text=chunk_dict.get("text", ""),
                source_type=chunk_dict.get("source_type", ""),
                source_filename=chunk_dict.get("source_filename") or "",
                source_url=chunk_dict.get("source_url") or "",
                section_path=list(chunk_dict.get("section_path") or []),
                page_number=chunk_dict.get("page_number") or 0,
                score=float(chunk_dict.get("score", 0.0)),
            )
            response.chunks.append(chunk_msg)
        return response

    @_record_metrics("HealthCheck")
    def HealthCheck(self, request, context):  # noqa: N802
        from apps.ingestion.embedder import _get_model
        from apps.qdrant_core.client import get_qdrant_client

        embedder_loaded = _get_model.cache_info().currsize > 0

        qdrant_ok = False
        try:
            get_qdrant_client().get_collections()
            qdrant_ok = True
        except Exception:
            pass

        return search_pb2.HealthCheckResponse(
            qdrant_ok=qdrant_ok,
            embedder_loaded=embedder_loaded,
            version=VERSION,
        )
