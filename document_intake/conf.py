"""Namespaced settings for the app.

The host project sets ``DOCUMENT_INTAKE = {...}`` in its settings. Any key it leaves out
falls back to the defaults below. Read values through ``intake_settings`` so overrides in
tests (``override_settings``) are picked up.
"""

from typing import Any

from django.conf import settings
from django.core.signals import setting_changed

DEFAULTS: dict[str, Any] = {
    # Upload validation
    "MAX_UPLOAD_BYTES": 25 * 1024 * 1024,
    "ALLOWED_MIME_TYPES": {
        "application/pdf": (".pdf",),
        "image/jpeg": (".jpg", ".jpeg"),
        "image/png": (".png",),
    },
    # Images above this many pixels are rejected (decompression-bomb guard).
    "MAX_IMAGE_PIXELS": 50_000_000,
    # Django storage alias for documents. It must not be publicly served.
    "STORAGE_ALIAS": "intake_private",
    "UPLOAD_PATH_PREFIX": "intake",
    # Extraction
    "PROVIDER_CLASS": "document_intake.providers.ollama.OllamaProvider",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_VISION_MODEL": "",
    "OLLAMA_TEXT_MODEL": "",
    "OLLAMA_TIMEOUT_SECONDS": 180,
    "OLLAMA_CONNECT_TIMEOUT_SECONDS": 10,
    # Context window requested from Ollama. Its own default is small and would silently
    # cut long statements, so we always set it explicitly.
    "OLLAMA_NUM_CTX": 16384,
    # How the JSON schema reaches the model. "prompt": written into the user message and
    # checked afterwards. "format": Ollama's constrained decoding. With "format", models
    # closed the transactions list early on long statements (2 of 27 rows), so it is opt-in.
    "OLLAMA_SCHEMA_MODE": "prompt",
    # In-call retries for transport errors / malformed JSON before the task-level retry.
    "OLLAMA_MAX_RETRIES": 2,
    "OLLAMA_RETRY_BACKOFF_SECONDS": 2.0,
    # Celery task retries for provider outages, with exponential backoff.
    "TASK_MAX_RETRIES": 3,
    "TASK_RETRY_BACKOFF_SECONDS": 30,
    # Size guards. Documents beyond these fail clearly instead of being silently truncated.
    "MAX_PAGES": 20,
    "MAX_VISION_PAGES": 4,
    "MAX_TEXT_CHARS": 48_000,
    "RENDER_DPI": 150,
    "MAX_IMAGE_EDGE_PX": 2000,
    # A PDF page counts as having a usable text layer above this many characters.
    "MIN_TEXT_CHARS_PER_PAGE": 40,
    "DEFAULT_CURRENCY": "CAD",
    # Validation
    "AMOUNT_TOLERANCE": "0.01",
    "EXPECTED_TAX_RATE": "0.13",  # Ontario HST, warning only
    # Access control. A portal swaps these for its own role / client-assignment rules.
    # PERMISSION_CLASSES: DRF permission classes for every API view.
    # DOCUMENT_SCOPE: function (user, queryset) -> queryset of documents the user may see.
    "PERMISSION_CLASSES": ("document_intake.api.permissions.IsOwnerOrStaff",),
    "DOCUMENT_SCOPE": "document_intake.services.access.default_document_scope",
    # REVIEW_PERMISSION_CLASSES: added on top for editing, approving, rejecting, exporting.
    # Standalone default lets the uploader review; a portal sets staff-only classes so
    # clients can upload but never approve accounting entries.
    "REVIEW_PERMISSION_CLASSES": ("document_intake.api.permissions.IsOwnerOrStaff",),
    # Feature flags
    "FEATURE_AUTO_CLASSIFY": True,
    "FEATURE_DUPLICATE_DETECTION": True,
    # Exact duplicates go straight to status "duplicate" and are not sent to the model.
    "SKIP_EXTRACTION_FOR_DUPLICATES": True,
}


class IntakeSettings:
    """Attribute access to DOCUMENT_INTAKE with defaults; cached until settings change."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name not in DEFAULTS:
            raise AttributeError(f"Unknown DOCUMENT_INTAKE setting: {name}")
        if name not in self._cache:
            user = getattr(settings, "DOCUMENT_INTAKE", {})
            self._cache[name] = user.get(name, DEFAULTS[name])
        return self._cache[name]

    def reload(self) -> None:
        self._cache.clear()


intake_settings = IntakeSettings()


def _reload(*, setting: str, **_: Any) -> None:
    if setting == "DOCUMENT_INTAKE":
        intake_settings.reload()


setting_changed.connect(_reload)
