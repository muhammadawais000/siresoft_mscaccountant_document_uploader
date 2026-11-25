"""Reviewer edits to the latest extraction result.

Every edit: locks the document, checks it is still under review, applies the change to
the latest result only, writes an audit entry (with before/after values, stored in the
database audit table, never in application logs) and re-runs validation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from document_intake.conf import intake_settings
from document_intake.exceptions import InvalidTransition
from document_intake.extraction import parsing
from document_intake.extraction.schemas import family
from document_intake.models import (
    AuditAction,
    Document,
    ExtractedField,
    ExtractedLine,
    ExtractionResult,
    ValidationIssue,
)

from . import audit, lifecycle, validation

LINE_FIELDS = (
    "date",
    "description",
    "debit",
    "credit",
    "amount",
    "balance",
    "tax",
    "category",
    # Credit card statements
    "posting_date",
    "transaction_number",
    "card_last4",
    "pre_tax",
)
TEXT_FIELDS = ("description", "category", "transaction_number", "card_last4")


class NotEditable(InvalidTransition):
    code = "not_editable"


def _lock_editable(document: Document) -> tuple[Document, ExtractionResult]:
    locked: Document = Document.objects.select_for_update().get(pk=document.pk)
    if locked.status not in lifecycle.EDITABLE:
        raise NotEditable(f"Documents in status '{locked.status}' cannot be edited.")
    result = locked.latest_result()
    if result is None:  # pragma: no cover - needs_review always has a result
        raise NotEditable("There is no extraction result to edit.")
    return locked, result


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def edit_field(document: Document, field_id: Any, value: str, actor: Any) -> ExtractedField:
    """Set a header field's value as typed by the reviewer."""
    with transaction.atomic():
        document, result = _lock_editable(document)
        row = result.fields.get(pk=field_id)
        before = row.value
        parsed = parsing.parse_header(row.key, value, str(intake_settings.DEFAULT_CURRENCY))
        row.value = parsed.value
        row.normalized_value = parsed.normalized_value
        row.edited_by, row.edited_at = actor, timezone.now()
        row.save()
        audit.record(
            AuditAction.EDIT,
            document,
            actor,
            target="field",
            key=row.key,
            version=result.version,
            before=before,
            after=row.value,
        )
        validation.validate_result(result)
    row.refresh_from_db()
    return row


def _apply_line_values(line: ExtractedLine, values: dict[str, Any], statement: bool) -> None:
    for name in LINE_FIELDS:
        if name in values:
            value = values[name]
            if name in TEXT_FIELDS and value is None:
                value = ""
            setattr(line, name, value)
    if statement and ("debit" in values or "credit" in values):
        # Statements derive the signed amount from the columns, never from free input.
        if line.debit is None and line.credit is None:
            line.amount = None
        else:
            line.amount = (line.credit or Decimal("0")) - (line.debit or Decimal("0"))


def edit_line(
    document: Document, line_id: Any, values: dict[str, Any], actor: Any
) -> ExtractedLine:
    with transaction.atomic():
        document, result = _lock_editable(document)
        line = result.lines.get(pk=line_id)
        statement = family(document.effective_doc_type) == "statement"
        before = {name: _jsonable(getattr(line, name)) for name in values if name in LINE_FIELDS}
        _apply_line_values(line, values, statement)
        line.edited_by, line.edited_at = actor, timezone.now()
        line.save()
        audit.record(
            AuditAction.EDIT,
            document,
            actor,
            target="line",
            line_no=line.line_no,
            version=result.version,
            before=before,
            after={name: _jsonable(getattr(line, name)) for name in before},
        )
        validation.validate_result(result)
    line.refresh_from_db()
    return line


def add_line(document: Document, values: dict[str, Any], actor: Any) -> ExtractedLine:
    """Append a row the model missed. Line numbers are never reused, even after deletes."""
    with transaction.atomic():
        document, result = _lock_editable(document)
        last = ExtractedLine.all_objects.filter(result=result).aggregate(n=Max("line_no"))["n"]
        line = ExtractedLine(result=result, line_no=(last or 0) + 1, raw={})
        _apply_line_values(line, values, family(document.effective_doc_type) == "statement")
        line.edited_by, line.edited_at = actor, timezone.now()
        line.save()
        audit.record(
            AuditAction.EDIT,
            document,
            actor,
            target="line",
            operation="add",
            line_no=line.line_no,
            version=result.version,
            after={name: _jsonable(getattr(line, name)) for name in LINE_FIELDS},
        )
        validation.validate_result(result)
    line.refresh_from_db()
    return line


def delete_line(document: Document, line_id: Any, actor: Any) -> None:
    """Soft delete: the row stays in the database for the audit trail."""
    with transaction.atomic():
        document, result = _lock_editable(document)
        line = result.lines.get(pk=line_id)
        line.soft_delete(actor)
        audit.record(
            AuditAction.EDIT,
            document,
            actor,
            target="line",
            operation="delete",
            line_no=line.line_no,
            version=result.version,
        )
        validation.validate_result(result)


def set_issue_resolved(
    document: Document, issue_id: Any, resolved: bool, actor: Any
) -> ValidationIssue:
    """Acknowledge (or re-open) a single issue."""
    with transaction.atomic():
        document, result = _lock_editable(document)
        issue = result.issues.get(pk=issue_id)
        issue.resolved = resolved
        issue.resolved_by = actor if resolved else None
        issue.resolved_at = timezone.now() if resolved else None
        issue.save()
        audit.record(
            AuditAction.EDIT,
            document,
            actor,
            target="issue",
            rule_code=issue.rule_code,
            resolved=resolved,
        )
    return issue
