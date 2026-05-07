import os
import sys

from django.apps import AppConfig


def _should_warm_embedder() -> bool:
    """Server-process-only heuristic. Skip for management commands, pytest,
    and celery workers — none of those need the 1.8 GB model preloaded.

    Server contexts that fall through to True:
      - gunicorn (web container)
      - ``python -m apps.grpc_service.server`` (grpc container)
      - ``manage.py runserver`` (local dev)
    """
    if os.environ.get("DJANGO_SKIP_WARMUP", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    argv0 = (sys.argv[0] if sys.argv else "").lower()
    # pytest / unittest harness — never warm.
    if "pytest" in argv0 or argv0.endswith("/pytest"):
        return False
    # celery worker / beat — no embedder needed.
    if argv0.endswith("/celery") or argv0.endswith("celery"):
        return False
    # Django management command via manage.py: only runserver should warm;
    # migrate / collectstatic / shell / test / makemigrations skip.
    if argv0.endswith("manage.py") and len(sys.argv) > 1 and sys.argv[1] != "runserver":
        return False
    return True


class IngestionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ingestion"
    label = "ingestion"

    def ready(self) -> None:
        if not _should_warm_embedder():
            return
        from apps.ingestion._warmup import start_warmup_in_background

        start_warmup_in_background()
