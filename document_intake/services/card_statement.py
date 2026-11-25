"""The credit card report: debits, credits, totals, balance and checks for a stored result.

Built from the current lines and header fields, so it follows every reviewer edit.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from document_intake.conf import intake_settings
from document_intake.extraction import parsing
from document_intake.extraction import statement_processor as processor
from document_intake.models import DocType, ExtractedField, ExtractedLine, ExtractionResult
from document_intake.validators import Issue
from document_intake.validators.types import Sev

ISSUE_CODES = {
    "closing_ok": ("balance_mismatch", "closing_balance"),
    "debits_ok": ("card_debits_mismatch", ""),
    "credits_ok": ("card_credits_mismatch", ""),
    "rows_ok": ("card_rows_mismatch", ""),
    "section_ok": ("card_section_mismatch", ""),
}


class CardReport(BaseModel):
    status: Literal["ok", "needs_review"]
    debits: list[processor.CardRow]
    credits: list[processor.CardRow]
    unclassified: list[processor.CardRow]
    totals: processor.Totals
    balance: processor.Balance
    checks: list[processor.Check]
    warnings: list[str]

    @property
    def has_tax(self) -> bool:
        return self.totals.total_tax is not None

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready. Amounts are decimal strings; credits are shown as positive."""
        data = self.model_dump(mode="json")
        details = data.pop("checks")
        for row, source in zip(data["credits"], self.credits, strict=True):
            row["amount"] = str(abs(source.amount)) if source.amount is not None else None
        return {
            **data,
            "checks": {check.code: check.ok for check in self.checks},
            "check_details": details,
        }


def is_card_result(result: ExtractionResult) -> bool:
    """Only results in the per-page format; older card results need a reprocess."""
    return (
        result.document.effective_doc_type == DocType.CREDIT_CARD_STATEMENT
        and "summary" in result.structured_data
    )


def _signed(line: ExtractedLine) -> Decimal | None:
    if line.debit is None and line.credit is None:
        return None
    return (line.debit or processor.ZERO) - (line.credit or processor.ZERO)


def _row(line: ExtractedLine) -> processor.CardRow:
    return processor.CardRow(
        id=str(line.pk),
        line_no=line.line_no,
        date=line.date,
        posting_date=line.posting_date,
        transaction_number=line.transaction_number,
        auth_number=line.auth_number,
        description=line.description,
        card_last4=line.card_last4,
        page=line.page,
        pre_tax=line.pre_tax,
        tax=line.tax,
        amount=_signed(line),
    )


def _header_amount(fields: Iterable[ExtractedField], key: str) -> Decimal | None:
    currency = str(intake_settings.DEFAULT_CURRENCY)
    for field in fields:
        if field.key == key:
            parsed = parsing.parse_header(key, field.value, currency).parsed
            return parsed if isinstance(parsed, Decimal) else None
    return None


_SUMMARY_AMOUNTS = (
    "payments",
    "payments_and_credits",
    "purchases_and_other_charges",
    "net_purchases",
    "cash_advances",
    "interest",
    "fees",
    "adjustments",
    "other_charges",
)


def _summary(raw: Mapping[str, Any], warnings: list[str]) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {}
    for key in _SUMMARY_AMOUNTS:
        try:
            values[key] = parsing.parse_printed_amount(raw.get(key))
        except parsing.ParseError:
            values[key] = None
            warnings.append(f"Printed {key.replace('_', ' ')} could not be read.")
    return values


def build_report(
    result: ExtractionResult,
    fields: Iterable[ExtractedField],
    lines: Iterable[ExtractedLine],
) -> CardReport | None:
    """None for anything that is not a card statement in the per-page format."""
    if not is_card_result(result):
        return None
    data = result.structured_data
    warnings: list[str] = []
    rows = [_row(line) for line in lines]
    fields = list(fields)
    previous = _header_amount(fields, "opening_balance")
    printed = _header_amount(fields, "closing_balance")
    pages = processor.split_pages(result.raw_text)
    checks = processor.run_checks(
        rows,
        previous=previous,
        printed_closing=printed,
        summary=_summary(data["summary"] or {}, warnings),
        sections=data.get("section_totals") or [],
        text_rows=processor.count_rows(pages),
    )
    debits, credits, unclassified = processor.split_rows(rows)
    totals = processor.compute_totals(rows)
    warnings += [
        f"Removed a line that is not a transaction: {row.get('description') or ''}".strip()
        for row in data.get("removed_rows") or []
    ]
    conflicts = [row for row in debits if row.amount and parsing.looks_like_credit(row.description)]
    warnings += [
        f"Line {row.line_no} reads like a payment or refund, but the printed amount is a charge."
        for row in conflicts
    ]
    warnings += [check.message for check in checks if check.failed]
    closing = None
    if previous is not None and not unclassified:  # never a balance with amounts left out
        closing = processor.calculated_closing(previous, totals)
    return CardReport(
        status="needs_review" if conflicts or any(c.failed for c in checks) else "ok",
        debits=debits,
        credits=credits,
        unclassified=unclassified,
        totals=totals,
        balance=processor.Balance(
            previous=previous,
            total_credits=totals.total_credits,
            total_debits=totals.total_debits,
            calculated_closing=closing,
            printed_closing=printed,
        ),
        checks=checks,
        warnings=warnings,
    )


def report_issues(report: CardReport, removed: Iterable[Mapping[str, Any]]) -> list[Issue]:
    """Failed checks block approval until acknowledged; unverifiable ones are warnings."""
    issues = []
    for check in report.checks:
        if not check.failed:
            continue
        code, key = ISSUE_CODES[check.code]
        if check.ok is None:
            code = code.replace("_mismatch", "_unverified")
        severity = Sev.ERROR if check.ok is False else Sev.WARNING
        issues.append(Issue(code, severity, check.message, key))
    issues += [
        Issue(
            "card_row_removed",
            Sev.WARNING,
            f"Removed a line that is not a transaction: {row.get('description') or ''}".strip(),
        )
        for row in removed
    ]
    return issues
