"""Step 5: validation persisted on stored results (fields, lines, issues)."""

from decimal import Decimal

import pytest
from django.utils import timezone

from document_intake.models import DocType, DocumentStatus, FieldStatus, Severity
from document_intake.services.extraction import run_extraction
from document_intake.services.validation import validate_result
from document_intake.tests.factories import DocumentFactory
from document_intake.tests.fakes import BANK_STATEMENT, RECEIPT, FakeProvider
from document_intake.tests.unit.test_sources import statement_pdf

pytestmark = pytest.mark.django_db


def extract(data, doc_type=DocType.BANK_STATEMENT):
    doc = DocumentFactory(content=statement_pdf(), doc_type=doc_type)
    return run_extraction(doc.id, FakeProvider(data))


def test_consistent_statement_has_no_issues() -> None:
    result = extract(BANK_STATEMENT)
    assert result.issues.count() == 0
    assert set(result.lines.values_list("status", flat=True)) == {FieldStatus.OK}
    assert result.document.status == DocumentStatus.NEEDS_REVIEW


def test_inconsistent_statement_is_flagged() -> None:
    result = extract({**BANK_STATEMENT, "closing_balance": "4300.00"})
    issue = result.issues.get(rule_code="balance_mismatch")
    assert issue.severity == Severity.ERROR
    closing = result.fields.get(key="closing_balance")
    assert closing.status == FieldStatus.ERROR
    assert "difference 100.00" in closing.status_reason
    # Still only "needs review" -- never approved or rejected automatically.
    result.document.refresh_from_db()
    assert result.document.status == DocumentStatus.NEEDS_REVIEW


def test_missing_and_reviewer_fields() -> None:
    result = extract({**RECEIPT, "vendor_name": None}, DocType.RECEIPT)
    statuses = dict(result.fields.values_list("key", "status"))
    assert statuses["vendor_name"] == FieldStatus.MISSING
    assert statuses["invoice_number"] == FieldStatus.MISSING  # null -> needs review, not filled
    assert statuses["account"] == FieldStatus.OK
    assert statuses["currency"] == FieldStatus.WARNING
    assert result.fields.get(key="currency").normalized_value == "CAD"


def test_unreadable_line_value_flags_line() -> None:
    data = {
        **BANK_STATEMENT,
        "transactions": [
            {**BANK_STATEMENT["transactions"][0], "debit": ">1500"},
            BANK_STATEMENT["transactions"][1],
        ],
    }
    result = extract(data)
    first = result.lines.get(line_no=1)
    assert first.status == FieldStatus.ERROR
    assert "unreadable" in first.status_reason
    assert result.issues.filter(rule_code="line_value", line_no=1).exists()


def test_revalidation_after_edit_clears_fixed_issue_and_keeps_resolved_flags() -> None:
    result = extract(
        {**BANK_STATEMENT, "closing_balance": "4300.00", "statement_period_start": None}
    )
    warning = result.issues.get(rule_code="required_missing")
    warning.resolved = True
    warning.save()

    closing = result.fields.get(key="closing_balance")
    closing.value = "4200.00"
    closing.save()
    validate_result(result)

    assert not result.issues.filter(rule_code="balance_mismatch").exists()
    assert result.issues.get(rule_code="required_missing").resolved is True
    closing.refresh_from_db()
    assert closing.status == FieldStatus.OK
    assert closing.normalized_value == "4200.00"


def test_edited_line_is_checked_from_typed_values() -> None:
    data = {
        **BANK_STATEMENT,
        "transactions": [
            {**BANK_STATEMENT["transactions"][0], "debit": "garbled"},
            BANK_STATEMENT["transactions"][1],
        ],
    }
    result = extract(data)
    line = result.lines.get(line_no=1)
    line.debit, line.amount = Decimal("1500.00"), Decimal("-1500.00")
    line.edited_at = timezone.now()
    line.save()

    validate_result(result)
    line.refresh_from_db()
    assert line.status == FieldStatus.OK
    assert result.issues.count() == 0


def test_soft_deleted_line_is_ignored() -> None:
    extra = {
        "date": "2026-09-20",
        "description": "Bogus",
        "debit": "100.00",
        "credit": None,
        "balance": None,
    }
    result = extract({**BANK_STATEMENT, "transactions": [*BANK_STATEMENT["transactions"], extra]})
    assert result.issues.filter(rule_code="balance_mismatch").exists()
    result.lines.get(line_no=3).soft_delete()
    validate_result(result)
    assert not result.issues.filter(rule_code="balance_mismatch").exists()
