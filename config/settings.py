"""Django settings for the qdrant_rag project."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "web"]),
    DJANGO_LOG_LEVEL=(str, "INFO"),
    POSTGRES_PORT=(int, 5432),
    QDRANT_GRPC_PORT=(int, 6334),
    QDRANT_HTTP_PORT=(int, 6333),
    QDRANT_PREFER_GRPC=(bool, True),
    BGE_USE_FP16=(bool, True),
    BGE_BATCH_SIZE=(int, 8),
    SEARCH_DEFAULT_TOP_K=(int, 5),
    SEARCH_MAX_TOP_K=(int, 20),
    SEARCH_THRESHOLD=(float, 0.65),
    SEARCH_PREFETCH_DENSE=(int, 50),
    SEARCH_PREFETCH_SPARSE=(int, 50),
    SEARCH_RRF_DENSE_WEIGHT=(float, 3.0),
    SEARCH_RRF_SPARSE_WEIGHT=(float, 1.0),
)

_env_file = BASE_DIR / ".env"
if _env_file.exists():
    env.read_env(_env_file)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["https://quadrant.bol7.com"],
)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core",
    "apps.tenants",
    "apps.documents",
    "apps.ingestion",
    "apps.qdrant_core",
    "apps.grpc_service",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AccessLogMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("POSTGRES_HOST"),
        "PORT": env.int("POSTGRES_PORT"),
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {
            "connect_timeout": 2,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

QDRANT = {
    "HOST": env("QDRANT_HOST"),
    "GRPC_PORT": env.int("QDRANT_GRPC_PORT"),
    "HTTP_PORT": env.int("QDRANT_HTTP_PORT"),
    "PREFER_GRPC": env.bool("QDRANT_PREFER_GRPC"),
    "API_KEY": env("QDRANT_API_KEY"),
}

BGE = {
    "MODEL_NAME": env("BGE_MODEL_NAME", default="BAAI/bge-m3"),
    "CACHE_DIR": env("BGE_CACHE_DIR", default="/app/.cache/bge"),
    "USE_FP16": env.bool("BGE_USE_FP16"),
    "DEVICE": env("BGE_DEVICE", default="cpu"),
    "BATCH_SIZE": env.int("BGE_BATCH_SIZE"),
}

SEARCH = {
    "DEFAULT_TOP_K": env.int("SEARCH_DEFAULT_TOP_K"),
    "MAX_TOP_K": env.int("SEARCH_MAX_TOP_K"),
    "THRESHOLD": env.float("SEARCH_THRESHOLD"),
    "PREFETCH_DENSE": env.int("SEARCH_PREFETCH_DENSE"),
    "PREFETCH_SPARSE": env.int("SEARCH_PREFETCH_SPARSE"),
    "RRF_DENSE_WEIGHT": env.float("SEARCH_RRF_DENSE_WEIGHT"),
    "RRF_SPARSE_WEIGHT": env.float("SEARCH_RRF_SPARSE_WEIGHT"),
}

CELERY_BROKER_URL = env("REDIS_URL")
CELERY_RESULT_BACKEND = env("REDIS_URL")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"

LOGGING_CONFIG = None

from apps.core.logging import configure_logging  # noqa: E402

configure_logging(debug=DEBUG, log_level=env("DJANGO_LOG_LEVEL"))
