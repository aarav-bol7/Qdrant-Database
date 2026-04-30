"""Thin recorder helpers — single import surface for middleware/handler/etc.

Phase 8b adds these to bridge the 8a registries (apps.core.metrics) to the
recording sites. Keeping recorders in their own module avoids circular
import risk when middleware/handler import from apps.core.metrics directly.
"""

from __future__ import annotations

from apps.core.metrics import (
    grpc_request_duration_seconds,
    grpc_requests_total,
    http_request_duration_seconds,
    http_requests_total,
    pipeline_phase_duration_seconds,
)


def record_http_request(
    method: str,
    endpoint: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    http_requests_total.labels(
        method=method,
        endpoint=endpoint,
        status_code=str(status_code),
    ).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration_seconds)


def record_grpc_request(rpc: str, status_code: str, duration_seconds: float) -> None:
    grpc_requests_total.labels(rpc=rpc, status_code=status_code).inc()
    grpc_request_duration_seconds.labels(rpc=rpc).observe(duration_seconds)


def record_pipeline_phase(phase: str, duration_seconds: float) -> None:
    pipeline_phase_duration_seconds.labels(phase=phase).observe(duration_seconds)
