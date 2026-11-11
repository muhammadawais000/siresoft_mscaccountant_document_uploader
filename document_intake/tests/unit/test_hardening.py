"""Step 9: cross-cutting guarantees (logging hygiene, query counts, demo seeding, CSP)."""

import logging
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse

from document_intake.models import Document, DocumentStatus
from document_intake.services.extraction import mark_failed, run_extraction
from document_intake.tests.factories import DocumentFactory, ExtractedLineFactory
from document_intake.tests.fakes import BANK_STATEMENT, FakeProvider
from document_intake.tests.unit.test_sources import statement_pdf

pytestmark = pytest.mark.django_db

SENSITIVE = [
    "5000.00",
    "4200.00",
    "1500.00",
    "Client payment",
    "Rent",
    "Sample Bank",
    "Transaction line",
]


def test_logs_never_contain_document_contents(caplog, auth_client, user) -> None:
    """Upload -> extract -> edit -> approve -> export, then scan every log record."""
    caplog.set_level(logging.DEBUG)
    doc = DocumentFactory(uploaded_by=user, content=statement_pdf(), doc_type="bank_statement")
    run_extraction(doc.id, FakeProvider({**BANK_STATEMENT, "closing_balance": "4300.00"}))
    field = doc.latest_result().fields.get(key="closing_balance")
    base = f"/api/v1/intake/documents/{doc.id}/"
    auth_client.patch(f"{base}fields/{field.id}/", {"value": "4200.00"}, format="json")
    auth_client.post(f"{base}approve/", {}, format="json")
    auth_client.get(f"{base}export.csv")
    mark_failed(DocumentFactory(uploaded_by=user).id, "Ollama timed out.")

    assert caplog.records, "expected some logging"
    for record in caplog.records:
        text = (
            record.getMessage()
            + " "
            + " ".join(
                str(v)
                for k, v in vars(record).items()
                if k not in logging.LogRecord.__dict__
                and k not in ("msg", "args", "message", "exc_info", "exc_text", "stack_info")
            )
        )
        for needle in SENSITIVE:
            assert needle not in text, (record.name, record.getMessage(), needle)


def test_chatty_parser_loggers_are_silenced() -> None:
    for name in ("pdfminer", "pdfplumber", "PIL"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_result_endpoint_query_count_is_constant(
    auth_client, user, django_assert_max_num_queries
) -> None:
    doc = DocumentFactory(uploaded_by=user, content=statement_pdf(), doc_type="bank_statement")
    result = run_extraction(doc.id, FakeProvider(BANK_STATEMENT))
    for n in range(3, 60):
        ExtractedLineFactory(result=result, line_no=n)
    with django_assert_max_num_queries(10):
        response = auth_client.get(reverse("document_intake_api:document-result", args=[doc.id]))
    assert len(response.json()["lines"]) == 59


def test_seed_demo_is_idempotent_and_shows_problems() -> None:
    out = StringIO()
    call_command("seed_demo", "--username", "demo", "--password", "demo-pass-xyz", stdout=out)
    call_command("seed_demo", "--username", "demo", stdout=out)
    docs = Document.objects.filter(uploaded_by__username="demo")
    assert docs.count() == 3
    assert set(docs.values_list("status", flat=True)) == {DocumentStatus.NEEDS_REVIEW}
    card = docs.get(doc_type="credit_card_statement").latest_result()
    assert card.issues.filter(severity="error").exists()
    bank = docs.get(doc_type="bank_statement").latest_result()
    assert bank.issues.count() == 0
    assert "exists: demo-bank-statement-sept-2026.pdf" in out.getvalue()


def test_ui_pages_send_csp(client, user) -> None:
    client.force_login(user)
    csp = client.get(reverse("document_intake:document-list"))["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
