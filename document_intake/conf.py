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
    # Django storage alias for documents. It must not be publicly served.
    "STORAGE_ALIAS": "intake_private",
    "UPLOAD_PATH_PREFIX": "intake",
    # Extraction
    "PROVIDER_CLASS": "document_intake.providers.ollama.OllamaProvider",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_VISION_MODEL": "",
    "OLLAMA_TEXT_MODEL": "",
    "OLLAMA_TIMEOUT_SECONDS": 180,
    "OLLAMA_MAX_RETRIES": 2,
    "MAX_PAGES": 20,
    "RENDER_DPI": 150,
    # A PDF page counts as having a usable text layer above this many characters.
    "MIN_TEXT_CHARS_PER_PAGE": 40,
    "DEFAULT_CURRENCY": "CAD",
    # Validation
    "AMOUNT_TOLERANCE": "0.01",
    "EXPECTED_TAX_RATE": "0.13",  # Ontario HST, warning only
    # Access control: dotted paths of DRF permission classes for the API.
    "PERMISSION_CLASSES": ("document_intake.api.permissions.IsOwnerOrStaff",),
    # Feature flags
    "FEATURE_AUTO_CLASSIFY": True,
    "FEATURE_DUPLICATE_DETECTION": True,
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
