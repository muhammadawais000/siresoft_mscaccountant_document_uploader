"""Test settings: eager Celery, fast hashing, isolated private storage."""

import os
import tempfile

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "postgres://intake:intake@localhost:55433/intake")

from .base import *

DEBUG = False
CELERY_TASK_ALWAYS_EAGER = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
STORAGES["intake_private"]["OPTIONS"]["location"] = tempfile.mkdtemp(prefix="intake-test-")

DOCUMENT_INTAKE = {
    **DOCUMENT_INTAKE,
    "OLLAMA_BASE_URL": "http://ollama.test",
    "OLLAMA_VISION_MODEL": "test-vision-model",
    "OLLAMA_TEXT_MODEL": "",
    "OLLAMA_TIMEOUT_SECONDS": 5,
}
