import json

from django.contrib import admin
from django.utils.html import format_html

from apps.documents.models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "doc_id",
        "tenant_id",
        "bot_id",
        "source_filename",
        "source_type",
        "status",
        "chunk_count",
        "uploaded_at",
    )
    list_filter = ("status", "source_type", "tenant_id")
    search_fields = ("doc_id", "source_filename", "source_url")
    ordering = ("-uploaded_at",)
    exclude = ("raw_payload",)
    readonly_fields = (
        "doc_id",
        "uploaded_at",
        "last_refreshed_at",
        "chunk_count",
        "item_count",
        "raw_payload_pretty",
    )

    @admin.display(description="Raw payload (uploaded JSON)")
    def raw_payload_pretty(self, obj):
        if obj.raw_payload is None:
            return "—"
        rendered = json.dumps(obj.raw_payload, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="white-space: pre-wrap; max-height: 600px; overflow: auto; '
            "background: #f8f8f8; padding: 12px; border: 1px solid #ddd; "
            'border-radius: 4px; font-family: monospace; font-size: 12px;">{}</pre>',
            rendered,
        )
