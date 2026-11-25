"""Run the validation engine on a stored result and persist issues and statuses.

Called after extraction and again after every reviewer edit. Issues that persist across
runs keep their ``resolved`` flag; issues that no longer apply are removed (they are
derived data, regenerated from the fields and lines, not financial records).
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from document_intake.conf import intake_settings
from document_intake.extraction import parsing
from document_intake.extraction.schemas import REVIEWER_KEYS, family
from document_intake.models import (
    DocType,
    ExtractedField,
    ExtractedLine,
    ExtractionResult,
    ValidationIssue,
)
from document_intake.validators import (
    FieldInput,
    Issue,
    LineInput,
    RuleConfig,
    derive_statuses,
    validate,
)

from . import card_statement

IssueKey = tuple[str, str, int | None, str]


def rule_config() -> RuleConfig:
    return RuleConfig(
        today=timezone.localdate(),
        tolerance=Decimal(str(intake_settings.AMOUNT_TOLERANCE)),
        expected_tax_rate=Decimal(str(intake_settings.EXPECTED_TAX_RATE)),
    )


def field_input(row: ExtractedField) -> FieldInput:
    currency = str(intake_settings.DEFAULT_CURRENCY)
    return parsing.parse_header(row.key, row.value, currency).as_input(row.key)


def line_input(row: ExtractedLine, doc_type: str) -> LineInput:
    """Unedited lines are re-parsed from the model's raw values (keeping parse problems);
    edited lines are checked from their typed columns."""
    statement = family(doc_type) == "statement"
    if row.edited_at is None and row.raw:
        rebuilt = parsing.build_lines(
            doc_type, {"transactions" if statement else "line_items": [row.raw]}
        )[0]
        rebuilt.line_no = row.line_no
        return rebuilt.as_input()
    line = parsing.LineRow(
        line_no=row.line_no,
        raw=row.raw,
        date=row.date,
        description=row.description,
        debit=row.debit,
        credit=row.credit,
        amount=row.amount,
        balance=row.balance,
        tax=row.tax,
        posting_date=row.posting_date,
    )
    if doc_type == DocType.CREDIT_CARD_STATEMENT:
        line.problems = parsing.card_line_problems(line)
    elif statement:
        line.problems = parsing.statement_line_problems(line)
    else:
        line.problems = parsing.item_line_problems(line)
    return line.as_input()


def _key(issue: Issue | ValidationIssue) -> IssueKey:
    return (issue.rule_code, issue.field_key or "", issue.line_no, issue.message)


def validate_result(result: ExtractionResult) -> list[ValidationIssue]:
    """Validate ``result``, store its issues and update every field and line status."""
    doc_type = result.document.effective_doc_type
    field_rows = list(result.fields.all())
    line_rows = list(result.lines.all())  # soft-deleted lines are excluded
    fields = {row.key: field_input(row) for row in field_rows}
    lines = [line_input(row, doc_type) for row in line_rows]

    report = card_statement.build_report(result, field_rows, line_rows)
    found = validate(doc_type, fields, lines, rule_config(), check_balance=report is None)
    if report is not None:
        removed = result.structured_data.get("removed_rows") or []
        found += card_statement.report_issues(report, removed)
    field_status, line_status = derive_statuses(fields, lines, found, reviewer_keys=REVIEWER_KEYS)

    with transaction.atomic():
        existing = {_key(issue): issue for issue in result.issues.all()}
        wanted = {_key(issue): issue for issue in found}
        stale = [issue.pk for key, issue in existing.items() if key not in wanted]
        if stale:
            ValidationIssue.objects.filter(pk__in=stale).delete()
        ValidationIssue.objects.bulk_create(
            ValidationIssue(
                result=result,
                rule_code=issue.rule_code,
                severity=issue.severity.value,
                field_key=issue.field_key,
                line_no=issue.line_no,
                message=issue.message[:500],
            )
            for key, issue in wanted.items()
            if key not in existing
        )

        for row in field_rows:
            state = field_status[row.key]
            row.status, row.status_reason = state.status, state.reason[:255]
            row.normalized_value = parsing.parse_header(
                row.key, row.value, str(intake_settings.DEFAULT_CURRENCY)
            ).normalized_value
        ExtractedField.objects.bulk_update(
            field_rows, ["status", "status_reason", "normalized_value"]
        )
        for line in line_rows:
            state = line_status[line.line_no]
            line.status, line.status_reason = state.status, state.reason[:255]
        ExtractedLine.objects.bulk_update(line_rows, ["status", "status_reason"])

    return list(result.issues.all())
