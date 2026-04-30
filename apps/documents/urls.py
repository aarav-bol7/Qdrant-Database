from django.urls import path

from apps.documents.views import (
    DeleteDocumentView,
    SearchDocumentsView,
    UploadDocumentView,
)

urlpatterns = [
    path(
        "tenants/<str:tenant_id>/bots/<str:bot_id>/documents",
        UploadDocumentView.as_view(),
        name="upload-document",
    ),
    path(
        "tenants/<str:tenant_id>/bots/<str:bot_id>/documents/<uuid:doc_id>",
        DeleteDocumentView.as_view(),
        name="delete-document",
    ),
    path(
        "tenants/<str:tenant_id>/bots/<str:bot_id>/search",
        SearchDocumentsView.as_view(),
        name="search-documents",
    ),
]
