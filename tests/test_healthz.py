from unittest import mock

import pytest
from django.test import Client


@pytest.mark.django_db
def test_healthz_returns_200_when_subsystems_healthy(client: Client) -> None:
    """Smoke test: /healthz reachable."""
    # In CI, Postgres + Qdrant are running. Locally, this assumes Compose is up.
    response = client.get("/healthz")
    assert response.status_code in (200, 503)
    body = response.json()
    assert "status" in body
    assert "components" in body
    assert "postgres" in body["components"]
    assert "qdrant" in body["components"]
    assert "embedder" in body["components"]
    assert "embedder_loaded" in body


@pytest.mark.django_db
def test_healthz_503_when_embedder_not_loaded(client: Client) -> None:
    """Even if postgres + qdrant are ok, /healthz must report 503 until the
    embedder warmup completes. This is the gate the Docker readiness probe
    waits on so the first user request lands on a warm model."""
    with mock.patch("apps.core.views._ping_postgres", return_value="ok"), \
         mock.patch("apps.core.views._ping_qdrant", return_value="ok"), \
         mock.patch("apps.ingestion._warmup.is_embedder_loaded", return_value=False):
        response = client.get("/healthz")
    assert response.status_code == 503
    body = response.json()
    assert body["embedder_loaded"] is False
    assert body["components"]["embedder"] == "warming"
    assert body["status"] == "warming"


@pytest.mark.django_db
def test_healthz_200_when_all_components_ok(client: Client) -> None:
    with mock.patch("apps.core.views._ping_postgres", return_value="ok"), \
         mock.patch("apps.core.views._ping_qdrant", return_value="ok"), \
         mock.patch("apps.ingestion._warmup.is_embedder_loaded", return_value=True):
        response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["embedder_loaded"] is True
    assert body["components"]["embedder"] == "ok"
    assert body["status"] == "ok"
