"""Typed exceptions for the upload pipeline.

The view catches `UploadError` subclasses and maps them to HTTP responses.
"""

from __future__ import annotations


class UploadError(Exception):
    """Base for all upload-pipeline errors. Never raised directly."""

    http_status = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidPayloadError(UploadError):
    http_status = 400
    code = "invalid_payload"


class NoEmbeddableContentError(UploadError):
    http_status = 422
    code = "no_embeddable_content"


class QdrantWriteError(UploadError):
    http_status = 500
    code = "qdrant_write_failed"


class EmbedderError(UploadError):
    http_status = 500
    code = "embedder_failed"


class ConcurrentUploadError(UploadError):
    http_status = 409
    code = "concurrent_upload"

    def __init__(
        self,
        message: str,
        retry_after: int = 5,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after = retry_after


class DocumentTooLargeError(UploadError):
    http_status = 422
    code = "too_many_chunks"


class DocumentNotFoundError(UploadError):
    """Document with the given doc_id doesn't exist in this tenant/bot."""

    http_status = 404
    code = "document_not_found"
