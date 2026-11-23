import os


# Defaults are for the local Compose demo. Override credentials for deployment.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-quakelens-local-demo-only")
DEBUG = False
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "rest_framework",
    "earthquakes",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "NAME": os.getenv("POSTGRES_DB", "quakelens"),
        "USER": os.getenv("POSTGRES_USER", "quakelens"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "quakelens-local-demo-only"),
        "OPTIONS": {"connect_timeout": 3},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = "UTC"
USE_TZ = True

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "earthquakes.views.api_exception_handler",
    "URL_FORMAT_OVERRIDE": None,
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_TASK_IGNORE_RESULT = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    "refresh-earthquake-catalogue": {
        "task": "earthquakes.tasks.refresh_catalogue",
        "schedule": max(1, int(os.getenv("QUAKE_REFRESH_SECONDS", "3600"))),
    },
}
