"""Verify Ollama connectivity and that the configured models are pulled.

Usage: ``python manage.py check_ollama [--probe]``. Exits non-zero on any problem.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from document_intake.conf import intake_settings
from document_intake.exceptions import IntakeError
from document_intake.providers.base import ModelRequest
from document_intake.providers.ollama import OllamaProvider


def _is_pulled(name: str, available: list[str]) -> bool:
    """Ollama lists ``model:tag``; an untagged name means ``:latest``."""
    wanted = name if ":" in name else f"{name}:latest"
    return wanted in available or name in available


class Command(BaseCommand):
    help = "Check that Ollama is reachable and the configured models are available."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--probe",
            action="store_true",
            help="Also run a tiny structured-output request against the text model.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        provider = OllamaProvider()
        self.stdout.write(f"Ollama URL: {provider.base_url}")
        try:
            available = provider.list_models()
        except IntakeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Models pulled: {', '.join(available) or '(none)'}")

        configured = {
            "OLLAMA_VISION_MODEL": str(intake_settings.OLLAMA_VISION_MODEL),
            "OLLAMA_TEXT_MODEL": str(intake_settings.OLLAMA_TEXT_MODEL),
        }
        if not configured["OLLAMA_VISION_MODEL"]:
            raise CommandError("OLLAMA_VISION_MODEL is not set.")

        problems = []
        for setting, model in configured.items():
            if not model:
                self.stdout.write(f"{setting}: not set (the vision model is used for text)")
                continue
            if _is_pulled(model, available):
                self.stdout.write(self.style.SUCCESS(f"{setting}: {model} is available"))
            else:
                problems.append(f"{setting}={model} is not pulled. Run: ollama pull {model}")
        if problems:
            raise CommandError("\n".join(problems))

        if options["probe"]:
            schema = {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            }
            try:
                response = provider.complete_json(
                    ModelRequest("Reply with JSON only.", 'Return {"ok": true}.', schema)
                )
            except IntakeError as exc:
                raise CommandError(f"Probe failed: {exc}") from exc
            self.stdout.write(
                self.style.SUCCESS(
                    f"Probe OK with {response.model_name} in {response.duration_ms} ms"
                )
            )
        self.stdout.write(self.style.SUCCESS("Ollama check passed."))
