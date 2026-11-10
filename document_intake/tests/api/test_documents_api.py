"""Step 3: upload / list / retrieve API, permissions and error format."""

import uuid

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from document_intake.models import DocType, Document, DocumentStatus
from document_intake.tests.conftest import make_image
from document_intake.tests.factories import DocumentFactory

pytestmark = pytest.mark.django_db

LIST_URL = reverse("document_intake_api:document-list")


def detail_url(doc_id) -> str:
    return reverse("document_intake_api:document-detail", args=[doc_id])


# ------------------------------------------------------------------ upload


def test_upload_requires_authentication(api_client, make_upload, pdf_bytes) -> None:
    response = api_client.post(LIST_URL, {"file": make_upload("a.pdf", pdf_bytes)})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authenticated"


def test_upload_pdf(auth_client, user, make_upload, pdf_bytes) -> None:
    owner = uuid.uuid4()
    response = auth_client.post(
        LIST_URL,
        {
            "file": make_upload("Sept statement.pdf", pdf_bytes),
            "doc_type": DocType.BANK_STATEMENT,
            "owner_reference": str(owner),
            "request_reference": "REQ-42",
        },
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["original_filename"] == "Sept statement.pdf"
    assert body["mime_type"] == "application/pdf"
    assert body["status"] == DocumentStatus.UPLOADED
    assert body["doc_type"] == DocType.BANK_STATEMENT
    assert body["owner_reference"] == str(owner)
    assert body["request_reference"] == "REQ-42"
    assert body["uploaded_by"] == user.pk
    assert "file" not in body  # never expose storage paths
    assert Document.objects.get(pk=body["id"]).uploaded_by == user


def test_upload_defaults_to_auto_detect(auth_client, make_upload) -> None:
    response = auth_client.post(LIST_URL, {"file": make_upload("r.jpg", make_image("JPEG"))})
    assert response.status_code == 201
    assert response.json()["doc_type"] == DocType.AUTO


def test_upload_rejects_spoofed_file_with_standard_error(auth_client, make_upload) -> None:
    response = auth_client.post(LIST_URL, {"file": make_upload("a.pdf", b"MZ" + b"\x00" * 64)})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upload_rejected"
    assert Document.all_objects.count() == 0


def test_upload_rejects_oversized(auth_client, make_upload, pdf_bytes) -> None:
    with override_settings(DOCUMENT_INTAKE={"MAX_UPLOAD_BYTES": 10}):
        response = auth_client.post(LIST_URL, {"file": make_upload("a.pdf", pdf_bytes)})
    assert response.status_code == 400
    assert "larger than" in response.json()["error"]["message"]


def test_upload_missing_file_is_field_error(auth_client) -> None:
    response = auth_client.post(LIST_URL, {"doc_type": DocType.RECEIPT})
    assert response.status_code == 400
    assert "file" in response.json()["error"]["details"]


def test_upload_rejects_bad_doc_type(auth_client, make_upload, pdf_bytes) -> None:
    response = auth_client.post(
        LIST_URL, {"file": make_upload("a.pdf", pdf_bytes), "doc_type": "payslip"}
    )
    assert response.status_code == 400
    assert "doc_type" in response.json()["error"]["details"]


def test_duplicate_upload_is_flagged(auth_client, make_upload, pdf_bytes) -> None:
    first = auth_client.post(LIST_URL, {"file": make_upload("a.pdf", pdf_bytes)}).json()
    second = auth_client.post(LIST_URL, {"file": make_upload("b.pdf", pdf_bytes)}).json()
    assert second["duplicate_of"] == first["id"]


def test_upload_is_throttled(user, make_upload, pdf_bytes) -> None:
    cache.clear()
    client = APIClient()
    client.force_authenticate(user)
    rates = {"intake_upload": "2/hour"}
    with patch_throttle_rates(rates):
        codes = [
            client.post(LIST_URL, {"file": make_upload(f"{i}.pdf", pdf_bytes)}).status_code
            for i in range(3)
        ]
    assert codes == [201, 201, 429]
    cache.clear()


def patch_throttle_rates(rates):
    from unittest.mock import patch

    from document_intake.api.throttles import UploadRateThrottle

    return patch.object(UploadRateThrottle, "THROTTLE_RATES", rates)


# ---------------------------------------------------------- permissions


def test_user_cannot_see_other_users_document(auth_client, other_user) -> None:
    theirs = DocumentFactory(uploaded_by=other_user)
    assert auth_client.get(detail_url(theirs.id)).status_code == 404
    assert auth_client.get(LIST_URL).json()["count"] == 0


def test_user_sees_own_documents_only(auth_client, user, other_user) -> None:
    mine = DocumentFactory(uploaded_by=user)
    DocumentFactory(uploaded_by=other_user)
    body = auth_client.get(LIST_URL).json()
    assert [row["id"] for row in body["results"]] == [str(mine.id)]
    assert auth_client.get(detail_url(mine.id)).status_code == 200


def test_staff_sees_all_documents(api_client, staff_user, user, other_user) -> None:
    DocumentFactory(uploaded_by=user)
    DocumentFactory(uploaded_by=other_user)
    api_client.force_authenticate(staff_user)
    assert api_client.get(LIST_URL).json()["count"] == 2


def test_soft_deleted_document_is_gone(auth_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user)
    doc.soft_delete(actor=user)
    assert auth_client.get(detail_url(doc.id)).status_code == 404
    assert auth_client.get(LIST_URL).json()["count"] == 0


def test_list_requires_authentication(api_client) -> None:
    assert api_client.get(LIST_URL).status_code == 403


def test_custom_scope_hook_is_used(auth_client, user) -> None:
    DocumentFactory(uploaded_by=user)
    hook = "document_intake.tests.api.test_documents_api.deny_all_scope"
    with override_settings(DOCUMENT_INTAKE={"DOCUMENT_SCOPE": hook}):
        assert auth_client.get(LIST_URL).json()["count"] == 0


def deny_all_scope(user, queryset):
    return queryset.none()


def test_custom_permission_classes_hook_is_used(auth_client) -> None:
    hook = ("rest_framework.permissions.IsAdminUser",)
    with override_settings(DOCUMENT_INTAKE={"PERMISSION_CLASSES": hook}):
        assert auth_client.get(LIST_URL).status_code == 403


# --------------------------------------------------------------- filters


def test_filters(auth_client, user) -> None:
    owner = uuid.uuid4()
    a = DocumentFactory(
        uploaded_by=user,
        original_filename="td-sept.pdf",
        doc_type=DocType.BANK_STATEMENT,
        status=DocumentStatus.NEEDS_REVIEW,
        owner_reference=owner,
    )
    b = DocumentFactory(
        uploaded_by=user,
        original_filename="costco.jpg",
        doc_type=DocType.RECEIPT,
        status=DocumentStatus.APPROVED,
    )

    def ids(**params) -> list[str]:
        return [row["id"] for row in auth_client.get(LIST_URL, params).json()["results"]]

    assert ids(status="needs_review") == [str(a.id)]
    assert set(ids(status=["needs_review", "approved"])) == {str(a.id), str(b.id)}
    assert ids(doc_type="receipt") == [str(b.id)]
    assert ids(owner_reference=str(owner)) == [str(a.id)]
    assert ids(search="COSTCO") == [str(b.id)]
    assert ids(ordering="original_filename") == [str(b.id), str(a.id)]


def test_invalid_filter_is_400(auth_client) -> None:
    response = auth_client.get(LIST_URL, {"status": "bogus"})
    assert response.status_code == 400
    assert "status" in response.json()["error"]["details"]


def test_list_query_count_is_constant(auth_client, user, django_assert_max_num_queries) -> None:
    for _ in range(5):
        DocumentFactory(uploaded_by=user)
    with django_assert_max_num_queries(4):
        auth_client.get(LIST_URL)


def test_openapi_schema_builds(api_client, staff_user) -> None:
    api_client.force_authenticate(staff_user)
    response = api_client.get(reverse("document_intake_api:schema"))
    assert response.status_code == 200
    assert b"/api/v1/intake/documents/" in response.content
