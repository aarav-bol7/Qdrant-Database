"""DRF serializers for the upload + HTTP search endpoints.

Phase 7.5 trims the upload schema to a generalized vector store core
and adds the HTTP search request/filters serializers (the same algorithm
gRPC uses; HTTP is a transport adapter).
"""

from __future__ import annotations

from rest_framework import serializers


class UploadItemSerializer(serializers.Serializer):
    content = serializers.CharField(allow_blank=False)
    section_path = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        default=list,
    )
    page_number = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    item_index = serializers.IntegerField(required=False, min_value=0)


class UploadBodySerializer(serializers.Serializer):
    SOURCE_TYPES = ["pdf", "docx", "url", "html", "csv", "faq", "image", "text"]
    REMOVED_FIELDS = {"language", "custom_metadata"}
    REMOVED_ITEM_FIELDS = {"language", "url", "item_type", "title"}

    doc_id = serializers.UUIDField(required=False)
    source_type = serializers.ChoiceField(choices=SOURCE_TYPES, default="text")
    source_filename = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    source_url = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    content_hash = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    items = UploadItemSerializer(many=True)

    def validate(self, attrs):
        forbidden_top = self.REMOVED_FIELDS & set(self.initial_data.keys())
        if forbidden_top:
            raise serializers.ValidationError(
                {
                    "error_code": "removed_field",
                    "message": (
                        f"Fields {sorted(forbidden_top)} were removed in Phase 7.5. "
                        "Drop them from the request body."
                    ),
                }
            )

        forbidden_id = {"tenant_id", "bot_id"} & set(self.initial_data.keys())
        if forbidden_id:
            raise serializers.ValidationError(
                {
                    "error_code": "id_in_body",
                    "message": (
                        f"Body must not contain {sorted(forbidden_id)} — these come from the URL path."
                    ),
                }
            )

        raw_items = self.initial_data.get("items") or []
        for i, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            forbidden_item = self.REMOVED_ITEM_FIELDS & set(raw.keys())
            if forbidden_item:
                raise serializers.ValidationError(
                    {
                        "error_code": "removed_field",
                        "message": (
                            f"items[{i}] contains removed fields {sorted(forbidden_item)}. Drop them."
                        ),
                    }
                )

        if not attrs.get("items"):
            raise serializers.ValidationError(
                {
                    "error_code": "empty_items",
                    "message": "items[] must not be empty.",
                }
            )
        return attrs


class SearchFiltersSerializer(serializers.Serializer):
    source_types = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    category = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    only_active = serializers.BooleanField(default=True)


class SearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(allow_blank=False)
    top_k = serializers.IntegerField(required=False, default=5, min_value=1, max_value=20)
    filters = SearchFiltersSerializer(required=False)

    def validate(self, attrs):
        filters = attrs.get("filters") or {}
        if filters and not filters.get("only_active", True):
            raise serializers.ValidationError(
                {
                    "error_code": "only_active_must_be_true",
                    "message": "filters.only_active must be true in v1.",
                }
            )
        if not attrs.get("query", "").strip():
            raise serializers.ValidationError(
                {
                    "error_code": "empty_query",
                    "message": "query must be non-empty after stripping.",
                }
            )
        return attrs
