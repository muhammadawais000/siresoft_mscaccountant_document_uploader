"""Step 4: the check_ollama management command."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from document_intake.exceptions import ProviderUnavailable
from document_intake.providers.base import ModelResponse

PROVIDER = "document_intake.management.commands.check_ollama.OllamaProvider"


def run(*args) -> str:
    out = StringIO()
    call_command("check_ollama", *args, stdout=out)
    return out.getvalue()


def test_passes_when_model_is_pulled() -> None:
    with patch(PROVIDER) as cls:
        cls.return_value.list_models.return_value = ["test-vision-model:latest"]
        cls.return_value.base_url = "http://ollama.test"
        output = run()
    assert "test-vision-model is available" in output
    assert "check passed" in output


def test_fails_when_model_missing() -> None:
    with patch(PROVIDER) as cls:
        cls.return_value.list_models.return_value = ["other:7b"]
        with pytest.raises(CommandError, match="ollama pull test-vision"):
            run()


def test_fails_when_text_model_missing() -> None:
    with (
        override_settings(DOCUMENT_INTAKE={"OLLAMA_VISION_MODEL": "v:1", "OLLAMA_TEXT_MODEL": "t"}),
        patch(PROVIDER) as cls,
    ):
        cls.return_value.list_models.return_value = ["v:1"]
        with pytest.raises(CommandError, match="OLLAMA_TEXT_MODEL=t"):
            run()


def test_fails_when_unreachable() -> None:
    with patch(PROVIDER) as cls:
        cls.return_value.list_models.side_effect = ProviderUnavailable("not reachable")
        with pytest.raises(CommandError, match="not reachable"):
            run()


def test_fails_without_configured_model() -> None:
    with (
        override_settings(DOCUMENT_INTAKE={"OLLAMA_VISION_MODEL": ""}),
        patch(PROVIDER) as cls,
    ):
        cls.return_value.list_models.return_value = []
        with pytest.raises(CommandError, match="not set"):
            run()


def test_probe() -> None:
    with patch(PROVIDER) as cls:
        cls.return_value.list_models.return_value = ["test-vision-model:latest"]
        cls.return_value.complete_json.return_value = ModelResponse({"ok": True}, "m", 12)
        assert "Probe OK with m in 12 ms" in run("--probe")
