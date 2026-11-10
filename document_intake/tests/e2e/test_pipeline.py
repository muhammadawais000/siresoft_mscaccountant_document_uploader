"""End-to-end: upload through the API -> Celery (eager) -> extraction -> result stored.

Only the model provider is faked; storage, routing, pdfplumber, rendering, parsing,
the task and the database are real.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from document_intake.models import Document, DocumentStatus, ExtractionMethod
from document_intake.tests.conftest import make_image
from document_intake.tests.fakes import BANK_STATEMENT, RECEIPT, FakeProvider
from document_intake.tests.unit.test_sources import statement_pdf

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.e2e]

LIST_URL = reverse("document_intake_api:document-list")


def upload(client, make_upload, name, content, provider, **extra):
    with patch("document_intake.services.extraction.get_provider", return_value=provider):
        response = client.post(LIST_URL, {"file": make_upload(name, content), **extra})
    assert response.status_code == 201, response.content
    return Document.objects.get(pk=response.json()["id"])


def test_text_pdf_statement_end_to_end(auth_client, make_upload) -> None:
    doc = upload(
        auth_client,
        make_upload,
        "td.pdf",
        statement_pdf(),
        FakeProvider(BANK_STATEMENT),
        doc_type="bank_statement",
    )
    assert doc.status == DocumentStatus.NEEDS_REVIEW
    assert doc.extraction_method == ExtractionMethod.PDF_TEXT
    result = doc.latest_result()
    assert result.lines.count() == 2
    detail = auth_client.get(reverse("document_intake_api:document-detail", args=[doc.id]))
    assert detail.json()["status"] == "needs_review"


def test_receipt_photo_end_to_end(auth_client, make_upload) -> None:
    provider = FakeProvider(RECEIPT, doc_type="receipt")
    doc = upload(auth_client, make_upload, "photo.jpg", make_image("JPEG", (600, 800)), provider)
    assert doc.status == DocumentStatus.NEEDS_REVIEW
    assert doc.detected_doc_type == "receipt"
    assert doc.extraction_method == ExtractionMethod.VISION
    assert [r.vision for r in provider.requests] == [True, True]


def test_duplicate_is_not_extracted(auth_client, make_upload) -> None:
    pdf = statement_pdf()
    upload(
        auth_client,
        make_upload,
        "a.pdf",
        pdf,
        FakeProvider(BANK_STATEMENT),
        doc_type="bank_statement",
    )
    provider = FakeProvider(BANK_STATEMENT)
    dup = upload(auth_client, make_upload, "b.pdf", pdf, provider, doc_type="bank_statement")
    assert dup.status == DocumentStatus.DUPLICATE
    assert dup.duplicate_of is not None
    assert provider.requests == []
    assert dup.results.count() == 0
