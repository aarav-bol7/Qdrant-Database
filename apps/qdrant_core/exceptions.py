"""Typed exceptions raised by the qdrant_core layer.

Callers in Phase 5/6/7 catch these specifically rather than the broad
`grpc.RpcError` or `qdrant_client` internal exceptions, so the upload
pipeline can react differently to transient connectivity vs. real
schema drift.
"""

from __future__ import annotations


class QdrantError(Exception):
    """Base class for all qdrant_core errors. Never raised directly."""


class QdrantConnectionError(QdrantError):
    """Transient connection failure: timeout, refused, network blip.

    Worth retrying with backoff. After exhausted retries, the wrapped
    exception is the last attempt's error.
    """


class CollectionSchemaMismatchError(QdrantError):
    """An existing collection's schema does not match the expected schema.

    Carries the diff so the operator can decide whether to migrate or
    recreate. NEVER auto-recreate — data loss risk.
    """

    def __init__(self, collection_name: str, diff: dict[str, str]) -> None:
        super().__init__(f"Collection {collection_name!r} schema mismatch: {diff}")
        self.collection_name = collection_name
        self.diff = diff


class QdrantOperationError(QdrantError):
    """Any other Qdrant-side failure (4xx-equivalent gRPC status, malformed
    request, internal server error). Not retried.
    """
