from django.db import models

from apps.tenants.validators import slug_validator


class Tenant(models.Model):
    tenant_id = models.CharField(
        max_length=40,
        primary_key=True,
        validators=[slug_validator],
    )
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tenant_id"]

    def __str__(self) -> str:
        return self.tenant_id


class Bot(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="bots",
    )
    bot_id = models.CharField(max_length=40, validators=[slug_validator])
    name = models.CharField(max_length=255)
    collection_name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "bot_id"], name="unique_bot_per_tenant"),
        ]
        indexes = [
            models.Index(fields=["tenant", "bot_id"]),
        ]
        ordering = ["tenant_id", "bot_id"]

    def save(self, *args, **kwargs):
        from apps.qdrant_core.naming import collection_name as _collection_name

        self.collection_name = _collection_name(self.tenant_id, self.bot_id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.tenant_id}/{self.bot_id}"
