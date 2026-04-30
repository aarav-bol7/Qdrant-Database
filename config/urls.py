from django.contrib import admin
from django.urls import include, path

from apps.core.metrics import metrics_view

urlpatterns = [
    path("admin/", admin.site.urls),
    # /metrics is unauthenticated in v1; Phase 8b's nginx config will scope to internal IPs.
    path("metrics", metrics_view, name="metrics"),
    path("", include("apps.core.urls")),
    path("v1/", include("apps.documents.urls")),
]
