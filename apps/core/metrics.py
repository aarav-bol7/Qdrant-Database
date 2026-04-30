"""Prometheus metrics registry + view.

Module-level Counter / Histogram / Gauge singletons backed by the default
prometheus_client REGISTRY. Hand-rolled — no django-prometheus magic.

Cardinality discipline: tenant_id / bot_id are NEVER labels (per-tenant
breakdown lives in structured logs). Allowed labels: endpoint, method,
status_code, phase, rpc.

Per-worker visibility note: gunicorn runs N workers, each with its own
process-local REGISTRY. /metrics from one worker reflects only that
worker's counters; the load balancer routes scrapes round-robin so the
view jitters. v1 acceptable; multiprocess mode is post-v1.

The /metrics route is unauthenticated. Phase 8b's nginx config will scope
it to internal IPs at the edge.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

http_requests_total = Counter(
    "qdrant_rag_http_requests_total",
    "Per-endpoint HTTP request count.",
    labelnames=("method", "endpoint", "status_code"),
)

http_request_duration_seconds = Histogram(
    "qdrant_rag_http_request_duration_seconds",
    "End-to-end HTTP request latency (seconds).",
    labelnames=("method", "endpoint"),
)

pipeline_phase_duration_seconds = Histogram(
    "qdrant_rag_pipeline_phase_duration_seconds",
    "Per-phase upload pipeline latency (seconds).",
    labelnames=("phase",),
)

grpc_requests_total = Counter(
    "qdrant_rag_grpc_requests_total",
    "Per-RPC gRPC request count.",
    labelnames=("rpc", "status_code"),
)

grpc_request_duration_seconds = Histogram(
    "qdrant_rag_grpc_request_duration_seconds",
    "gRPC request latency (seconds).",
    labelnames=("rpc",),
)

search_results_count = Histogram(
    "qdrant_rag_search_results_count",
    "Distribution of total_candidates returned per search call.",
)

search_threshold_used = Gauge(
    "qdrant_rag_search_threshold_used",
    "Last reported threshold_used from a search call.",
)

embedder_loaded = Gauge(
    "qdrant_rag_embedder_loaded",
    "1 if BGE-M3 is loaded in the current process; 0 otherwise.",
)
embedder_loaded.set(0)


def record_http_request(
    *, endpoint: str, method: str, status_code: int, duration_seconds: float
) -> None:
    http_requests_total.labels(method=method, endpoint=endpoint, status_code=str(status_code)).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration_seconds)


def record_pipeline_phase(*, phase: str, seconds: float) -> None:
    pipeline_phase_duration_seconds.labels(phase=phase).observe(seconds)


def record_grpc(*, rpc: str, status_code: str, duration_seconds: float) -> None:
    grpc_requests_total.labels(rpc=rpc, status_code=status_code).inc()
    grpc_request_duration_seconds.labels(rpc=rpc).observe(duration_seconds)


def record_search_results(*, total_candidates: int, threshold_used: float) -> None:
    search_results_count.observe(float(total_candidates))
    search_threshold_used.set(float(threshold_used))


def set_embedder_loaded(loaded: bool) -> None:
    embedder_loaded.set(1 if loaded else 0)


def metrics_view(request):
    """Render Prometheus exposition. Lazy import HttpResponse to avoid Django setup at module load."""
    from django.http import HttpResponse

    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
