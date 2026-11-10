"""Step 1 smoke tests: settings, request IDs, health endpoint, error envelope."""

import pytest
from django.test import override_settings
from django.urls import reverse

from document_intake.conf import DEFAULTS, intake_settings


def test_intake_settings_fall_back_to_defaults() -> None:
    assert intake_settings.MAX_PAGES == DEFAULTS["MAX_PAGES"]


def test_intake_settings_follow_override() -> None:
    with override_settings(DOCUMENT_INTAKE={"MAX_PAGES": 3}):
        assert intake_settings.MAX_PAGES == 3
    assert intake_settings.MAX_PAGES == DEFAULTS["MAX_PAGES"]


def test_unknown_setting_raises() -> None:
    with pytest.raises(AttributeError):
        _ = intake_settings.NOT_A_SETTING


@pytest.mark.django_db
def test_health_endpoint_is_public(api_client) -> None:
    response = api_client.get(reverse("document_intake_api:health"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_request_id_generated_and_echoed(api_client) -> None:
    generated = api_client.get(reverse("document_intake_api:health"))
    assert len(generated["X-Request-ID"]) == 32

    echoed = api_client.get(reverse("document_intake_api:health"), HTTP_X_REQUEST_ID="abc-123")
    assert echoed["X-Request-ID"] == "abc-123"

    unsafe = api_client.get(reverse("document_intake_api:health"), HTTP_X_REQUEST_ID="bad id\n")
    assert unsafe["X-Request-ID"] != "bad id\n"


@pytest.mark.django_db
def test_error_envelope_shape(api_client) -> None:
    response = api_client.post(reverse("document_intake_api:health"))
    assert response.status_code == 405
    assert response.json() == {
        "error": {
            "code": "method_not_allowed",
            "message": 'Method "POST" not allowed.',
            "details": {},
        }
    }


@pytest.mark.django_db
def test_schema_requires_login(api_client) -> None:
    response = api_client.get(reverse("document_intake_api:schema"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_ui_requires_login(client) -> None:
    response = client.get(reverse("document_intake:document-list"))
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
