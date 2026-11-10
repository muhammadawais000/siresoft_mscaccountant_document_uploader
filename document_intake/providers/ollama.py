"""Ollama HTTP provider: ``POST /api/chat`` with a JSON-schema ``format`` and temperature 0."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx
import jsonschema

from document_intake.conf import intake_settings
from document_intake.exceptions import ExtractionError, MalformedModelOutput, ProviderUnavailable

from .base import ExtractionProvider, ModelRequest, ModelResponse, conform

logger = logging.getLogger(__name__)


class OllamaProvider(ExtractionProvider):
    name = "ollama"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.base_url = str(intake_settings.OLLAMA_BASE_URL).rstrip("/")
        self.timeout = float(intake_settings.OLLAMA_TIMEOUT_SECONDS)
        self.max_retries = int(intake_settings.OLLAMA_MAX_RETRIES)
        self.backoff = float(intake_settings.OLLAMA_RETRY_BACKOFF_SECONDS)
        self._client = client or httpx.Client(base_url=self.base_url, timeout=self.timeout)

    # ------------------------------------------------------------------ public

    def model_for(self, *, vision: bool) -> str:
        vision_model = str(intake_settings.OLLAMA_VISION_MODEL)
        model = vision_model if vision else str(intake_settings.OLLAMA_TEXT_MODEL or vision_model)
        if not model:
            raise ExtractionError("No Ollama model is configured (set OLLAMA_VISION_MODEL).")
        return model

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        model = self.model_for(vision=request.vision)
        payload = self._payload(model, request)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(self.backoff * (2 ** (attempt - 1)))
            started = time.monotonic()
            try:
                content = self._post_chat(payload)
                data = self._parse(content, request.schema)
            except (ProviderUnavailable, MalformedModelOutput) as exc:
                last_error = exc
                logger.warning(
                    "ollama attempt failed",
                    extra={"attempt": attempt + 1, "model": model, "error": exc.code},
                )
                continue
            return ModelResponse(
                data=data,
                model_name=model,
                duration_ms=int((time.monotonic() - started) * 1000),
                raw_content=content,
            )
        if last_error is None:  # pragma: no cover - loop always runs at least once
            raise ExtractionError("Ollama call did not run.")
        raise last_error

    def list_models(self) -> list[str]:
        """Names of the models pulled on the Ollama server (``GET /api/tags``)."""
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Ollama is not reachable at {self.base_url}.") from exc
        return [str(m["name"]) for m in response.json().get("models", [])]

    # ----------------------------------------------------------------- helpers

    def _payload(self, model: str, request: ModelRequest) -> dict[str, Any]:
        user_message: dict[str, Any] = {"role": "user", "content": request.user}
        if request.images:
            user_message["images"] = [
                base64.b64encode(img).decode("ascii") for img in request.images
            ]
        return {
            "model": model,
            "stream": False,
            "format": request.schema,
            "options": {"temperature": 0, "num_ctx": int(intake_settings.OLLAMA_NUM_CTX)},
            "messages": [{"role": "system", "content": request.system}, user_message],
        }

    def _post_chat(self, payload: dict[str, Any]) -> str:
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable("Ollama timed out.") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailable(f"Ollama is not reachable at {self.base_url}.") from exc

        if response.status_code == 404:
            # Ollama answers 404 when the model is not pulled: a config error, not retryable.
            raise ExtractionError(f"Model '{payload['model']}' is not available on Ollama.")
        if response.status_code >= 500:
            raise ProviderUnavailable(f"Ollama returned HTTP {response.status_code}.")
        if response.status_code >= 400:
            raise ExtractionError(f"Ollama rejected the request (HTTP {response.status_code}).")

        try:
            body = response.json()
            content = body["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise MalformedModelOutput("Ollama response had no message content.") from exc
        if not isinstance(content, str) or not content.strip():
            raise MalformedModelOutput("The model returned empty output.")
        return content

    @staticmethod
    def _parse(content: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise MalformedModelOutput("The model output is not valid JSON.") from exc
        if not isinstance(data, dict):
            raise MalformedModelOutput("The model output is not a JSON object.")
        shaped: dict[str, Any] = conform(data, schema)
        try:
            jsonschema.validate(shaped, schema)
        except jsonschema.ValidationError as exc:
            path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            raise MalformedModelOutput(
                f"The model output does not match the schema at {path}."
            ) from exc
        return shaped
