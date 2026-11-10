"""Provider abstraction: business logic depends on this interface, never on Ollama.

A new engine (vLLM, a cloud OCR service) implements ``ExtractionProvider`` and is
selected with ``DOCUMENT_INTAKE["PROVIDER_CLASS"]``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from django.utils.module_loading import import_string

from document_intake.conf import intake_settings


@dataclass(frozen=True)
class ModelRequest:
    """One structured-output call.

    ``images`` are raw PNG/JPEG bytes; the provider handles encoding.
    ``vision`` selects the vision model; otherwise the text model is used.
    """

    system: str
    user: str
    schema: dict[str, Any]
    images: tuple[bytes, ...] = ()
    vision: bool = False


@dataclass(frozen=True)
class ModelResponse:
    data: dict[str, Any]
    model_name: str
    duration_ms: int
    raw_content: str = field(repr=False, default="")


class ExtractionProvider(abc.ABC):
    """Returns JSON that validates against the request's schema, or raises.

    Raises:
        ProviderUnavailable: transport failure or timeout (retryable).
        MalformedModelOutput: output is not JSON or does not match the schema.
        ExtractionError: configuration problems (not retryable).
    """

    name: str = "base"

    @abc.abstractmethod
    def complete_json(self, request: ModelRequest) -> ModelResponse: ...

    @abc.abstractmethod
    def model_for(self, *, vision: bool) -> str:
        """The model name that a request with this ``vision`` flag would use."""


def get_provider() -> ExtractionProvider:
    cls = import_string(intake_settings.PROVIDER_CLASS)
    provider: ExtractionProvider = cls()
    return provider


def conform(data: Any, schema: dict[str, Any]) -> Any:
    """Shape model output to the schema before validation.

    Missing object properties become null (the prompt's own meaning for "not legible") and
    unknown properties are dropped. Types are not coerced; validation still rejects them.
    """
    if schema.get("type") == "object" and isinstance(data, dict):
        props: dict[str, Any] = schema.get("properties", {})
        return {key: conform(data.get(key), sub) for key, sub in props.items()}
    if schema.get("type") == "array" and isinstance(data, list):
        return [conform(item, schema.get("items", {})) for item in data]
    if schema.get("type") == "array" and data is None:
        return []
    return data
