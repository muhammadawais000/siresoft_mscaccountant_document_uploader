"""Receipts on card statement purchases: attach, show missing, view, remove, keep on reprocess."""

import copy
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from document_intake.models import AuditAction, AuditLog, DocType, TransactionReceipt
from document_intake.services import lifecycle
from document_intake.services.extraction import run_extraction
from document_intake.tests.card_fixtures import BMO_PAGES, BMO_TEXT, PageProvider, make_pdf
from document_intake.tests.conftest import make_image
from document_intake.tests.e2e.test_card_statement import api, extract
from document_intake.tests.factories import DocumentFactory

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}


def png(name="receipt.png"):
    return SimpleUploadedFile(name, make_image(), content_type="image/png")


def url(name, doc, **kwargs):
    return reverse(f"document_intake:{name}", kwargs={"pk": doc.pk, **kwargs})


def purchase(doc, amount="14.98"):
    return doc.latest_result().lines.get(debit=Decimal(amount))


@pytest.fixture
def client_of(user):
    """The client who uploaded the statement (not staff)."""
    c = Client()
    c.force_login(user)
    return c


def test_missing_receipts_are_shown_on_every_purchase(client_of, user) -> None:
    doc, _ = extract(user, "bmo")
    html = client_of.get(url("document-review", doc)).content.decode()
    assert html.count("Receipt missing") == 25
    assert "Receipts: 0 of 25 · 25 missing" in html


def test_client_attaches_a_receipt(client_of, auth_client, user) -> None:
    doc, _ = extract(user, "bmo")
    line = purchase(doc)
    response = client_of.post(
        url("attach-receipt", doc, line_id=line.pk), {"file": png("arzon.png")}, **HTMX
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "arzon.png" in html
    assert html.count("Receipt missing") == 24
    assert "Receipts: 1 of 25 · 24 missing" in html
    receipt = TransactionReceipt.objects.get(line=line)
    assert receipt.uploaded_by == user
    assert AuditLog.objects.filter(
        document=doc, action=AuditAction.UPLOAD, metadata__target="receipt"
    ).exists()
    row = next(r for r in api(auth_client, doc)["debits"] if r["id"] == str(line.pk))
    assert row["receipts"] == [{"id": str(receipt.pk), "filename": "arzon.png"}]
    assert row["receipt_missing"] is False


def test_receipt_file_is_served_after_the_permission_check(client_of, user, other_user) -> None:
    doc, _ = extract(user, "bmo")
    receipt_line = purchase(doc)
    client_of.post(url("attach-receipt", doc, line_id=receipt_line.pk), {"file": png()}, **HTMX)
    receipt = TransactionReceipt.objects.get()
    response = client_of.get(url("receipt-file", doc, receipt_id=receipt.pk))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["Cache-Control"] == "private, no-store"
    stranger = Client()
    stranger.force_login(other_user)
    assert stranger.get(url("receipt-file", doc, receipt_id=receipt.pk)).status_code == 404
    assert (
        stranger.post(
            url("attach-receipt", doc, line_id=receipt_line.pk), {"file": png()}, **HTMX
        ).status_code
        == 404
    )


def test_remove_receipt(client_of, user) -> None:
    doc, _ = extract(user, "bmo")
    line = purchase(doc)
    client_of.post(url("attach-receipt", doc, line_id=line.pk), {"file": png()}, **HTMX)
    receipt = TransactionReceipt.objects.get()
    html = client_of.post(url("remove-receipt", doc, receipt_id=receipt.pk), **HTMX)
    assert html.content.decode().count("Receipt missing") == 25
    assert not TransactionReceipt.objects.exists()
    assert TransactionReceipt.all_objects.get().deleted_by == user


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (
            lambda: SimpleUploadedFile("r.exe", b"MZ...", content_type="application/pdf"),
            "not allowed",
        ),
        (lambda: SimpleUploadedFile("r.png", b"", content_type="image/png"), "empty"),
    ],
)
def test_bad_files_are_refused(client_of, user, make, message) -> None:
    doc, _ = extract(user, "bmo")
    line = purchase(doc)
    response = client_of.post(url("attach-receipt", doc, line_id=line.pk), {"file": make()}, **HTMX)
    assert response["HX-Retarget"] == "#messages"
    assert message in response.content.decode()
    assert not TransactionReceipt.objects.exists()


def test_same_file_twice_and_credit_rows_are_refused(client_of, user) -> None:
    doc, _ = extract(user, "bmo")
    line = purchase(doc)
    client_of.post(url("attach-receipt", doc, line_id=line.pk), {"file": png()}, **HTMX)
    again = client_of.post(url("attach-receipt", doc, line_id=line.pk), {"file": png()}, **HTMX)
    assert "already attached" in again.content.decode()
    credit = doc.latest_result().lines.get(credit=Decimal("613.03"))
    refused = client_of.post(url("attach-receipt", doc, line_id=credit.pk), {"file": png()}, **HTMX)
    assert "purchases only" in refused.content.decode()
    assert TransactionReceipt.objects.count() == 1


def test_receipts_can_still_be_added_after_approval(client_of, user, staff_user) -> None:
    doc, _ = extract(user, "bmo")
    lifecycle.approve(doc, staff_user)
    line = purchase(doc)
    response = client_of.post(url("attach-receipt", doc, line_id=line.pk), {"file": png()}, **HTMX)
    assert response.status_code == 200
    assert TransactionReceipt.objects.count() == 1


def test_receipts_follow_their_purchase_into_a_new_version(client_of, user) -> None:
    doc, _ = extract(user, "bmo")
    line = purchase(doc, "1413.88")
    client_of.post(url("attach-receipt", doc, line_id=line.pk), {"file": png()}, **HTMX)
    lifecycle.queue_reprocess(doc, user)
    run_extraction(doc.id, PageProvider(copy.deepcopy(BMO_PAGES)))
    doc.refresh_from_db()
    assert doc.latest_result().version == 2
    moved = TransactionReceipt.objects.get()
    assert moved.line.result.version == 2
    assert moved.line.debit == Decimal("1413.88")


def test_bank_statements_have_no_receipt_column(client_of, user) -> None:
    doc = DocumentFactory(
        uploaded_by=user, content=make_pdf(BMO_TEXT), doc_type=DocType.BANK_STATEMENT
    )
    from document_intake.tests.fakes import BANK_STATEMENT, FakeProvider

    run_extraction(doc.id, FakeProvider(BANK_STATEMENT))
    html = client_of.get(url("document-review", doc)).content.decode()
    assert "Receipt missing" not in html
