"""Pytest-only settings overlay for the qdrant_rag suite.

Imports the production settings then forces an in-memory SQLite database so
`@pytest.mark.django_db` works without a Postgres container running. The
spec test only asserts `status_code in (200, 503)`, neither of which depends
on Postgres-specific behavior.

Pointed at via `[tool.pytest.ini_options].DJANGO_SETTINGS_MODULE` in pyproject.
"""

from config.settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
