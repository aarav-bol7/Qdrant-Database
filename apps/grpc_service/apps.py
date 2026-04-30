from django.apps import AppConfig


class GrpcServiceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.grpc_service"
    label = "grpc_service"
