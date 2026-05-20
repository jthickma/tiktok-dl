"""Minimal Django settings for tiktok-dl web UI.

No database, no auth, no admin — just templates, static files, and a single app.
"""
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET", "tiktok-dl-dev-secret-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "archive",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

ROOT_URLCONF = "tiktokdl.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [],
        },
    },
]

WSGI_APPLICATION = "tiktokdl.wsgi.application"

# No DB
DATABASES = {}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# App-specific paths (overridable by env)
CHANNELS_FILE = Path(os.environ.get("CHANNELS_FILE", BASE_DIR / "channels.txt"))
ARCHIVE_FILE = Path(os.environ.get("ARCHIVE_FILE", BASE_DIR / "archive.txt"))
DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", BASE_DIR / "downloads"))
LOG_FILE = Path(os.environ.get("LOG_FILE", BASE_DIR / "logs" / "download.log"))
PID_FILE = Path(os.environ.get("PID_FILE", "/tmp/tiktok-dl.pid"))
PENDING_FILE = Path(os.environ.get("PENDING_FILE", "/tmp/tiktok-dl.pending"))
LAST_RUN_FILE = Path(os.environ.get("LAST_RUN_FILE", "/tmp/tiktok-dl.last-run"))
REQUEST_SCRIPT = os.environ.get("REQUEST_SCRIPT", str(BASE_DIR / "request_download.sh"))

# Disable CSRF for POST endpoints — this app is single-user behind a private network.
# Set DJANGO_REQUIRE_CSRF=1 to re-enable.
REQUIRE_CSRF = os.environ.get("DJANGO_REQUIRE_CSRF", "0") == "1"
if not REQUIRE_CSRF:
    MIDDLEWARE = [m for m in MIDDLEWARE if "csrf" not in m.lower()]
