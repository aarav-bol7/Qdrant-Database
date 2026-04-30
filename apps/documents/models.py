import uuid

from django.db import models

from apps.tenants.models import Bot
from apps.tenants.validators import slug_validator


class Document(models.Model):
    PENDING = "pending"
    ACTIVE = "active"
    DELETED = "deleted"
    FAILED = "failed"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (ACTIVE, "Active"),
        (DELETED, "Deleted"),
        (FAILED, "Failed"),
    ]

    doc_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot_ref = models.ForeignKey(
        Bot,
        on_delete=models.CASCADE,
        related_name="documents",
    )

    tenant_id = models.CharField(max_length=40, validators=[slug_validator])
    bot_id = models.CharField(max_length=40, validators=[slug_validator])

    source_type = models.CharField(max_length=20)
    source_filename = models.CharField(max_length=500, null=True, blank=True)
    source_url = models.TextField(null=True, blank=True)
    content_hash = models.CharField(max_length=80)
    chunk_count = models.IntegerField(default=0)
    item_count = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    error_message = models.TextField(null=True, blank=True)
    raw_payload = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Validated upload body as posted by the scraper. "
            "Debug aid only — do not read this for ingestion / chunking. "
            "Set on create/replace; untouched on no_change."
        ),
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    last_refreshed_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant_id", "bot_id"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        label = self.source_filename or self.source_url or "—"
        return f"{str(self.doc_id)[:8]} ({label})"
