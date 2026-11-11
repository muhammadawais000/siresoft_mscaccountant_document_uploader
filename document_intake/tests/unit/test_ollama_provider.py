"""Step 4: Ollama provider with the HTTP layer mocked (httpx.MockTransport)."""

import base64
import json

import httpx
import pytest
from django.test import override_settings

from document_intake.exceptions import ExtractionError, MalformedModelOutput, ProviderUnavailable
from document_intake.extraction.schemas import RECEIPT_INVOICE_SCHEMA
from document_intake.providers.base import ModelRequest, conform, get_provider
from document_intake.providers.ollama import OllamaProvider

SCHEMA = {
    "type": "object",
    "properties": {"vendor": {"type": ["string", "null"]}, "total": {"type": ["string", "null"]}},
    "required": ["vendor", "total"],
    "additionalProperties": False,
}


def chat_reply(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"message": {"role": "assistant", "content": content}})


def make_provider(handler) -> tuple[OllamaProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(record))
    return OllamaProvider(client=client), seen


def request(**kwargs) -> ModelRequest:
    return ModelRequest(system="sys", user="usr", schema=SCHEMA, **kwargs)


def test_success_payload_and_result() -> None:
    provider, seen = make_provider(lambda r: chat_reply('{"vendor": "Costco", "total": "1.00"}'))
    response = provider.complete_json(request(images=(b"PNGDATA",), vision=True))

    assert response.data == {"vendor": "Costco", "total": "1.00"}
    assert response.model_name == "test-vision-model"
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/chat"
    assert body["model"] == "test-vision-model"
    assert body["stream"] is False
    assert body["format"] == SCHEMA
    assert body["options"]["temperature"] == 0
    assert body["options"]["num_ctx"] > 2048
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1]["images"] == [base64.b64encode(b"PNGDATA").decode()]


def test_text_requests_fall_back_to_vision_model() -> None:
    provider, seen = make_provider(lambda r: chat_reply('{"vendor": null, "total": null}'))
    provider.complete_json(request())
    assert json.loads(seen[0].content)["model"] == "test-vision-model"
    assert "images" not in json.loads(seen[0].content)["messages"][1]


def test_text_model_used_when_configured() -> None:
    with override_settings(DOCUMENT_INTAKE={"OLLAMA_VISION_MODEL": "v", "OLLAMA_TEXT_MODEL": "t"}):
        provider, seen = make_provider(lambda r: chat_reply('{"vendor": null, "total": null}'))
        provider.complete_json(request())
    assert json.loads(seen[0].content)["model"] == "t"


def test_missing_keys_become_null_and_extras_dropped() -> None:
    provider, _ = make_provider(lambda r: chat_reply('{"vendor": "A", "note": "extra"}'))
    assert provider.complete_json(request()).data == {"vendor": "A", "total": None}


def test_malformed_json_is_retried_then_raised() -> None:
    provider, seen = make_provider(lambda r: chat_reply("not json {"))
    with pytest.raises(MalformedModelOutput):
        provider.complete_json(request())
    assert len(seen) == 3  # 1 + OLLAMA_MAX_RETRIES


def test_recovers_after_one_bad_answer() -> None:
    answers = iter(["oops", '{"vendor": "A", "total": "2.00"}'])
    provider, seen = make_provider(lambda r: chat_reply(next(answers)))
    assert provider.complete_json(request()).data["total"] == "2.00"
    assert len(seen) == 2


@pytest.mark.parametrize("content", ["", "   ", "[1, 2]", '{"vendor": 5, "total": null}'])
def test_bad_output_shapes(content: str) -> None:
    provider, _ = make_provider(lambda r: chat_reply(content))
    with pytest.raises(MalformedModelOutput):
        provider.complete_json(request())


def test_missing_message_field() -> None:
    provider, _ = make_provider(lambda r: httpx.Response(200, json={"done": True}))
    with pytest.raises(MalformedModelOutput):
        provider.complete_json(request())


def test_timeout_is_provider_unavailable() -> None:
    def boom(r):
        raise httpx.ReadTimeout("slow", request=r)

    provider, seen = make_provider(boom)
    with pytest.raises(ProviderUnavailable, match="timed out"):
        provider.complete_json(request())
    assert len(seen) == 3


def test_connection_error_is_provider_unavailable() -> None:
    def refuse(r):
        raise httpx.ConnectError("refused", request=r)

    provider, _ = make_provider(refuse)
    with pytest.raises(ProviderUnavailable, match="not reachable"):
        provider.complete_json(request())


def test_server_error_is_retried() -> None:
    provider, seen = make_provider(lambda r: httpx.Response(503))
    with pytest.raises(ProviderUnavailable):
        provider.complete_json(request())
    assert len(seen) == 3


def test_model_not_pulled_is_not_retried() -> None:
    provider, seen = make_provider(lambda r: httpx.Response(404, json={"error": "not found"}))
    with pytest.raises(ExtractionError, match="not available"):
        provider.complete_json(request())
    assert len(seen) == 1


def test_bad_request_is_not_retried() -> None:
    provider, seen = make_provider(lambda r: httpx.Response(400))
    with pytest.raises(ExtractionError, match="rejected"):
        provider.complete_json(request())
    assert len(seen) == 1


def test_no_model_configured() -> None:
    with override_settings(DOCUMENT_INTAKE={"OLLAMA_VISION_MODEL": ""}):
        provider, seen = make_provider(lambda r: chat_reply("{}"))
        with pytest.raises(ExtractionError, match="No Ollama model"):
            provider.complete_json(request())
    assert seen == []


def test_list_models() -> None:
    provider, _ = make_provider(
        lambda r: httpx.Response(200, json={"models": [{"name": "a:latest"}, {"name": "b:7b"}]})
    )
    assert provider.list_models() == ["a:latest", "b:7b"]


def test_list_models_unreachable() -> None:
    provider, _ = make_provider(lambda r: httpx.Response(500))
    with pytest.raises(ProviderUnavailable):
        provider.list_models()


def test_conform_nested_schema() -> None:
    shaped = conform(
        {"vendor_name": "A", "line_items": [{"amount": "1.00"}]}, RECEIPT_INVOICE_SCHEMA
    )
    assert shaped["total"] is None
    assert shaped["line_items"][0] == {
        "description": None,
        "quantity": None,
        "unit_price": None,
        "amount": "1.00",
        "tax": None,
    }
    assert conform({"line_items": None}, RECEIPT_INVOICE_SCHEMA)["line_items"] == []


def test_get_provider_uses_setting() -> None:
    hook = "document_intake.tests.fakes.FakeProvider"
    with override_settings(DOCUMENT_INTAKE={"PROVIDER_CLASS": hook}):
        assert get_provider().name == "fake"


def test_default_client_uses_short_connect_timeout() -> None:
    provider = OllamaProvider()
    assert provider._client.timeout.connect == 10
    assert provider._client.timeout.read == 5  # OLLAMA_TIMEOUT_SECONDS in test settings


def test_connect_timeout_reports_unreachable() -> None:
    def hang(r):
        raise httpx.ConnectTimeout("no route", request=r)

    provider, _ = make_provider(hang)
    with pytest.raises(ProviderUnavailable, match=r"not reachable at http://ollama\.test"):
        provider.complete_json(request())
