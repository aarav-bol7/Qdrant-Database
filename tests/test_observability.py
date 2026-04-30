"""Phase 8a observability tests: middleware + metrics + structlog enrichment."""

from __future__ import annotations

import logging
import re
import uuid

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def client():
    return APIClient()


class TestRequestIDMiddleware:
    def test_x_request_id_generated_when_absent(self, client):
        # Use an arbitrary path that goes through middleware (not /metrics or /healthz).
        # An invalid slug returns 400 quickly without needing a real Qdrant.
        r = client.post("/v1/tenants/Bad-Slug/bots/x/search", {"query": "x"}, format="json")
        rid = r.headers.get("X-Request-ID")
        assert rid is not None
        # UUIDv4 shape
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", rid
        )

    def test_x_request_id_echoed_when_present(self, client):
        provided = "abc-123-def-456"
        r = client.post(
            "/v1/tenants/Bad-Slug/bots/x/search",
            {"query": "x"},
            format="json",
            HTTP_X_REQUEST_ID=provided,
        )
        assert r.headers.get("X-Request-ID") == provided

    def test_x_request_id_truncated_at_100_chars(self, client):
        long_rid = "a" * 200
        r = client.post(
            "/v1/tenants/Bad-Slug/bots/x/search",
            {"query": "x"},
            format="json",
            HTTP_X_REQUEST_ID=long_rid,
        )
        echoed = r.headers.get("X-Request-ID")
        assert echoed is not None
        assert len(echoed) <= 100

    def test_contextvar_isolation_across_requests(self, client):
        rid1 = f"first-{uuid.uuid4().hex[:8]}"
        rid2 = f"second-{uuid.uuid4().hex[:8]}"
        r1 = client.post(
            "/v1/tenants/Bad-Slug/bots/x/search",
            {"query": "x"},
            format="json",
            HTTP_X_REQUEST_ID=rid1,
        )
        r2 = client.post(
            "/v1/tenants/Bad-Slug/bots/x/search",
            {"query": "x"},
            format="json",
            HTTP_X_REQUEST_ID=rid2,
        )
        assert r1.headers.get("X-Request-ID") == rid1
        assert r2.headers.get("X-Request-ID") == rid2


class TestMetricsEndpoint:
    def test_returns_prometheus_format(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers.get("Content-Type", "").startswith("text/plain")
        body = r.content.decode()
        # Confirm the metric names appear; counter/histogram families are emitted
        # with _total / _count / _bucket suffixes by prometheus_client.
        for needle in (
            "qdrant_rag_http_requests_total",
            "qdrant_rag_http_request_duration_seconds",
            "qdrant_rag_pipeline_phase_duration_seconds",
            "qdrant_rag_grpc_requests_total",
            "qdrant_rag_grpc_request_duration_seconds",
            "qdrant_rag_search_results_count",
            "qdrant_rag_search_threshold_used",
            "qdrant_rag_embedder_loaded",
        ):
            assert needle in body, f"missing metric: {needle}"

    def test_metrics_endpoint_includes_request_id_header_post_polish(self, client):
        # Phase 8b polish: /metrics keeps X-Request-ID (only /static/ is excluded
        # from RequestIDMiddleware). Prometheus scrapers ignore the header; it's harmless.
        r = client.get("/metrics")
        assert r.headers.get("X-Request-ID") is not None


class TestRequestIDPostPolish:
    """Phase 8b: RequestID exclusion narrowed to /static/ only."""

    def test_healthz_includes_request_id_post_polish(self, client):
        r = client.get("/healthz")
        # /healthz now keeps the header (correlation when probes fail)
        assert r.headers.get("X-Request-ID") is not None
        # /healthz is STILL excluded from access log (high probe volume).


class TestAccessLogExclusions:
    def _capture(self, caplog, fn):
        with caplog.at_level(logging.INFO, logger="apps.core.access"):
            fn()
        return [
            r
            for r in caplog.records
            if r.name == "apps.core.access" and r.message == "request_completed"
        ]

    def test_metrics_excluded_from_access_log(self, client, caplog):
        records = self._capture(caplog, lambda: client.get("/metrics"))
        assert records == []

    def test_healthz_excluded_from_access_log(self, client, caplog):
        records = self._capture(caplog, lambda: client.get("/healthz"))
        assert records == []

    def test_request_completed_emitted_for_non_excluded_path(self, client, caplog):
        # Use bad-slug path: returns 400 quickly without Qdrant or BGE-M3.
        records = self._capture(
            caplog,
            lambda: client.post(
                "/v1/tenants/Bad-Slug/bots/x/search",
                {"query": "x"},
                format="json",
            ),
        )
        assert len(records) == 1
        rec = records[0]
        # AccessLogMiddleware emits via logger.info(..., extra=...). Verify keys.
        assert getattr(rec, "method", None) == "POST"
        assert getattr(rec, "path", "").startswith("/v1/tenants/")
        assert getattr(rec, "status_code", 0) == 400
        assert getattr(rec, "duration_ms", None) is not None
        assert isinstance(getattr(rec, "phases", None), dict)
