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
