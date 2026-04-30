"""Shared pytest fixtures for the qdrant_rag test suite."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client


@pytest.fixture(autouse=True)
def _allow_testserver() -> None:
    """Allow Django's default `testserver` HTTP_HOST through ALLOWED_HOSTS.

    Production .env keeps ALLOWED_HOSTS=localhost,127.0.0.1,web per spec; the
    test client uses 'testserver' which would otherwise 400 with DEBUG=False.
    """
    if "testserver" not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]


@pytest.fixture
def client() -> Client:
    return Client()
