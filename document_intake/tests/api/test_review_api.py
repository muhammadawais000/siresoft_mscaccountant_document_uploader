"""Step 6: result, edits, actions, file download and CSV export endpoints."""

import csv
import io
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

from document_intake.models import (
    AuditAction,
    AuditLog,
    DocType,
    DocumentStatus,
    ExtractedLine,
    FieldStatus,
)
from document_intake.services.extraction import run_extraction
from document_intake.tests.factories import DocumentFactory
from document_intake.tests.fakes import BANK_STATEMENT, RECEIPT, FakeProvider
from document_intake.tests.unit.test_sources import statement_pdf

pytestmark = pytest.mark.django_db
S = DocumentStatus


def url(name, doc, **kwargs):
    return reverse(f"document_intake_api:document-{name}", kwargs={"pk": doc.pk, **kwargs})


def extracted(user, data=BANK_STATEMENT, doc_type=DocType.BANK_STATEMENT, **kwargs):
    doc = DocumentFactory(uploaded_by=user, content=statement_pdf(), doc_type=doc_type, **kwargs)
    run_extraction(doc.id, FakeProvider(data))
    doc.refresh_from_db()
    return doc


def field_id(doc, key):
    return doc.latest_result().fields.get(key=key).pk


def line_id(doc, line_no):
    return doc.latest_result().lines.get(line_no=line_no).pk


# --------------------------------------------------------------------- result


def test_result_before_extraction_is_404(auth_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user)
    response = auth_client.get(url("result", doc))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_result_payload(auth_client, user) -> None:
    doc = extracted(user, {**BANK_STATEMENT, "closing_balance": "4300.00"})
    body = auth_client.get(url("result", doc)).json()
    assert body["version"] == 1
    assert body["doc_type"] == "bank_statement"
    assert body["document_status"] == "needs_review"
    assert [f["key"] for f in body["header_fields"]][:2] == ["institution_name", "account_last4"]
    assert [line["line_no"] for line in body["lines"]] == [1, 2]
    assert body["lines"][0]["debit"] == "1500.00"
    assert body["issues"][0]["rule_code"] == "balance_mismatch"
    assert "raw_text" not in body


def test_result_specific_version(auth_client, user) -> None:
    doc = extracted(user)
    assert auth_client.get(url("result", doc), {"version": 1}).status_code == 200
    assert auth_client.get(url("result", doc), {"version": 9}).status_code == 404
    assert auth_client.get(url("result", doc), {"version": "x"}).status_code == 404


def test_result_of_other_users_document_is_404(auth_client, other_user) -> None:
    doc = extracted(other_user)
    assert auth_client.get(url("result", doc)).status_code == 404


def test_list_shows_open_issue_counts(auth_client, user, django_assert_max_num_queries) -> None:
    extracted(user, {**BANK_STATEMENT, "closing_balance": "4300.00"})
    extracted(user, {**BANK_STATEMENT, "statement_period_end": None})
    with django_assert_max_num_queries(4):
        rows = auth_client.get(reverse("document_intake_api:document-list")).json()["results"]
    counts = sorted((r["open_issues"], r["open_errors"]) for r in rows)
    assert counts == [(1, 0), (1, 1)]


# ---------------------------------------------------------------------- edits


def test_edit_field_revalidates_and_audits(auth_client, user) -> None:
    doc = extracted(user, {**BANK_STATEMENT, "closing_balance": "4300.00"})
    response = auth_client.patch(
        url("field", doc, field_id=field_id(doc, "closing_balance")),
        {"value": "4,200.00"},
        format="json",
    )
    assert response.status_code == 200
    body = response.json()
    closing = next(f for f in body["header_fields"] if f["key"] == "closing_balance")
    assert closing["value"] == "4,200.00"
    assert closing["normalized_value"] == "4200.00"
    assert closing["status"] == FieldStatus.OK
    assert closing["edited_by"] == user.pk
    assert body["issues"] == []
    entry = AuditLog.objects.get(document=doc, action=AuditAction.EDIT)
    assert entry.metadata["before"] == "4300.00"
    assert entry.metadata["after"] == "4,200.00"


def test_edit_field_with_bad_value_is_flagged_not_rejected(auth_client, user) -> None:
    doc = extracted(user)
    body = auth_client.patch(
        url("field", doc, field_id=field_id(doc, "opening_balance")),
        {"value": "abc"},
        format="json",
    ).json()
    opening = next(f for f in body["header_fields"] if f["key"] == "opening_balance")
    assert opening["status"] == FieldStatus.ERROR


def test_edit_unknown_field_is_404(auth_client, user) -> None:
    doc = extracted(user)
    other = extracted(user)
    response = auth_client.patch(
        url("field", doc, field_id=field_id(other, "currency")), {"value": "USD"}, format="json"
    )
    assert response.status_code == 404


def test_edit_line_recomputes_statement_amount(auth_client, user) -> None:
    doc = extracted(user)
    body = auth_client.patch(
        url("line", doc, line_id=line_id(doc, 1)), {"debit": "1400.00"}, format="json"
    ).json()
    line = body["lines"][0]
    assert line["debit"] == "1400.00"
    assert line["amount"] == "-1400.00"
    assert line["description"] == "Rent"  # untouched (partial update)
    assert {i["rule_code"] for i in body["issues"]} >= {"balance_mismatch"}


@pytest.mark.parametrize(
    "payload",
    [{"debit": "-5.00"}, {"credit": "1.234"}, {"date": "03/09/2026"}, {"balance": "abc"}],
)
def test_edit_line_rejects_invalid_values(auth_client, user, payload) -> None:
    doc = extracted(user)
    response = auth_client.patch(url("line", doc, line_id=line_id(doc, 1)), payload, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["details"]


def test_add_and_delete_lines(auth_client, user) -> None:
    doc = extracted(user)
    body = auth_client.post(
        url("lines", doc),
        {"date": "2026-09-20", "description": "Fee", "debit": "10.00"},
        format="json",
    ).json()
    assert [line["line_no"] for line in body["lines"]] == [1, 2, 3]
    assert body["lines"][2]["amount"] == "-10.00"
    assert any(i["rule_code"] == "balance_mismatch" for i in body["issues"])

    third = body["lines"][2]["id"]
    body = auth_client.delete(url("line", doc, line_id=third)).json()
    assert [line["line_no"] for line in body["lines"]] == [1, 2]
    assert body["issues"] == []
    assert ExtractedLine.all_objects.get(pk=third).is_deleted

    body = auth_client.post(
        url("lines", doc), {"description": "x", "credit": "1.00"}, format="json"
    ).json()
    assert body["lines"][-1]["line_no"] == 4  # numbers are never reused


def test_resolve_issue(auth_client, user) -> None:
    doc = extracted(user, {**BANK_STATEMENT, "statement_period_end": None})
    issue = doc.latest_result().issues.get()
    body = auth_client.patch(
        url("issue", doc, issue_id=issue.pk), {"resolved": True}, format="json"
    ).json()
    assert body["issues"][0]["resolved"] is True
    assert body["issues"][0]["resolved_by"] == user.pk


def test_edits_blocked_after_approval(auth_client, user) -> None:
    doc = extracted(user)
    auth_client.post(url("approve", doc), {}, format="json")
    response = auth_client.patch(
        url("field", doc, field_id=field_id(doc, "currency")), {"value": "USD"}, format="json"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_editable"


# -------------------------------------------------------------------- actions


def test_approve_blocked_then_acknowledged(auth_client, user) -> None:
    doc = extracted(user, {**BANK_STATEMENT, "closing_balance": "4300.00"})
    blocked = auth_client.post(url("approve", doc), {}, format="json")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "approval_blocked"

    ok = auth_client.post(
        url("approve", doc), {"acknowledge_errors": True, "note": "bank typo"}, format="json"
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == S.APPROVED


def test_reject_requires_reason(auth_client, user) -> None:
    doc = extracted(user)
    assert auth_client.post(url("reject", doc), {}, format="json").status_code == 400
    response = auth_client.post(url("reject", doc), {"reason": "Not ours"}, format="json")
    assert response.json()["status"] == S.REJECTED


def test_clarify_and_resume(auth_client, user) -> None:
    doc = extracted(user)
    body = auth_client.post(url("clarify", doc), {"question": "Page 2?"}, format="json").json()
    assert body["status"] == S.NEEDS_CLARIFICATION
    body = auth_client.post(url("resume-review", doc), format="json").json()
    assert body["status"] == S.NEEDS_REVIEW


def test_mark_duplicate(auth_client, user, other_user) -> None:
    doc = extracted(user)
    theirs = DocumentFactory(uploaded_by=other_user)
    response = auth_client.post(
        url("mark-duplicate", doc), {"duplicate_of": str(theirs.id)}, format="json"
    )
    assert response.status_code == 404  # cannot link to a document you cannot see
    mine = DocumentFactory(uploaded_by=user, content=b"orig")
    body = auth_client.post(
        url("mark-duplicate", doc), {"duplicate_of": str(mine.id)}, format="json"
    ).json()
    assert body["status"] == S.DUPLICATE
    assert body["duplicate_of"] == str(mine.id)


def test_reprocess(auth_client, user, django_capture_on_commit_callbacks) -> None:
    doc = extracted(user)
    with (
        patch("document_intake.tasks.extract_document.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        body = auth_client.post(url("reprocess", doc), format="json").json()
    assert body["status"] == S.UPLOADED
    delay.assert_called_once_with(str(doc.id))


def test_wrong_state_action_is_409(auth_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, status=S.PROCESSING)
    response = auth_client.post(url("reject", doc), {"reason": "x"}, format="json")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"


def test_review_permission_hook(auth_client, user) -> None:
    doc = extracted(user)
    staff_only = ("rest_framework.permissions.IsAdminUser",)
    with override_settings(DOCUMENT_INTAKE={"REVIEW_PERMISSION_CLASSES": staff_only}):
        assert auth_client.get(url("result", doc)).status_code == 200  # can still view
        assert auth_client.post(url("approve", doc), {}, format="json").status_code == 403
        response = auth_client.patch(
            url("field", doc, field_id=field_id(doc, "currency")),
            {"value": "USD"},
            format="json",
        )
        assert response.status_code == 403


# ------------------------------------------------------------------------ file


def test_file_inline_preview(auth_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, original_filename='Sept "TD" é.pdf')
    response = auth_client.get(url("file", doc))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"%PDF-1.4 test document"
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline")
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["X-Frame-Options"] == "SAMEORIGIN"
    entry = AuditLog.objects.get(document=doc, action=AuditAction.DOWNLOAD)
    assert entry.metadata == {"disposition": "inline"}


def test_file_download_attachment(auth_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user)
    response = auth_client.get(url("file", doc), {"download": "1"})
    assert response["Content-Disposition"].startswith("attachment")


def test_file_of_other_user_is_404(auth_client, other_user) -> None:
    assert auth_client.get(url("file", DocumentFactory(uploaded_by=other_user))).status_code == 404


def test_file_requires_login(api_client) -> None:
    assert api_client.get(url("file", DocumentFactory())).status_code == 403


# ---------------------------------------------------------------------- export


def read_csv(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.content.decode())))


def test_export_requires_approval(auth_client, user) -> None:
    doc = extracted(user)
    response = auth_client.get(url("export-csv", doc))
    assert response.status_code == 409


def test_export_statement(auth_client, user) -> None:
    doc = extracted(user)
    auth_client.post(url("approve", doc), {}, format="json")
    response = auth_client.get(url("export-csv", doc))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert 'filename="statement-v1.csv"' in response["Content-Disposition"]
    assert read_csv(response) == [
        ["Date", "Description", "Debit", "Credit", "Balance"],
        ["2026-09-03", "Rent", "1500.00", "", "3500.00"],
        ["2026-09-15", "Client payment", "", "700.00", "4200.00"],
    ]
    doc.refresh_from_db()
    assert doc.status == S.EXPORTED
    # Re-exporting is allowed and audited each time.
    assert auth_client.get(url("export-csv", doc)).status_code == 200
    assert AuditLog.objects.filter(document=doc, action=AuditAction.EXPORT).count() == 2


def test_export_receipt_with_reviewer_account(auth_client, user) -> None:
    doc = extracted(user, RECEIPT, DocType.RECEIPT)
    auth_client.patch(
        url("field", doc, field_id=field_id(doc, "account")),
        {"value": "5100 Supplies"},
        format="json",
    )
    auth_client.post(url("approve", doc), {}, format="json")
    rows = read_csv(auth_client.get(url("export-csv", doc)))
    assert rows == [
        ["Date", "Vendor", "Account", "Description", "Subtotal", "Tax", "Total", "Currency"],
        ["2026-09-03", "Hardware Store", "5100 Supplies", "", "100.00", "13.00", "113.00", "CAD"],
    ]


def test_export_neutralises_formula_injection(auth_client, user) -> None:
    data = {
        **BANK_STATEMENT,
        "transactions": [
            {**BANK_STATEMENT["transactions"][0], "description": '=HYPERLINK("http://x")'},
            BANK_STATEMENT["transactions"][1],
        ],
    }
    doc = extracted(user, data)
    auth_client.post(url("approve", doc), {}, format="json")
    rows = read_csv(auth_client.get(url("export-csv", doc)))
    assert rows[1][1].startswith("'=")
