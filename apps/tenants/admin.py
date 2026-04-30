from django.contrib import admin

from apps.tenants.models import Bot, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("tenant_id", "name", "created_at")
    search_fields = ("tenant_id", "name")
    readonly_fields = ("created_at",)
    ordering = ("tenant_id",)


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ("tenant_id", "bot_id", "name", "collection_name", "created_at")
    list_filter = ("tenant",)
    search_fields = ("bot_id", "name", "collection_name")
    readonly_fields = ("collection_name", "created_at")
    ordering = ("tenant_id", "bot_id")

    def tenant_id(self, obj):
        return obj.tenant_id

    tenant_id.short_description = "tenant_id"
    tenant_id.admin_order_field = "tenant_id"
