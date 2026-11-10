"""Rules for receipts and supplier invoices."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from .common import ZERO, close, day, field_problems, fmt, future_date, line_problems, money
from .types import FieldInput, Issue, LineInput, RuleConfig, Sev

TAX_KEYS = ("gst", "hst", "pst")

REQUIRED_RECEIPT = {"vendor_name": Sev.ERROR, "invoice_date": Sev.ERROR, "total": Sev.ERROR}
REQUIRED_INVOICE = {**REQUIRED_RECEIPT, "invoice_number": Sev.WARNING}


def _taxes(fields: Mapping[str, FieldInput]) -> tuple[Decimal, bool]:
    """Sum of the taxes shown, and whether any tax value was present at all."""
    values = [money(fields, key) for key in TAX_KEYS]
    present = [v for v in values if v is not None]
    return sum(present, ZERO), bool(present)


def check_totals(fields: Mapping[str, FieldInput], cfg: RuleConfig) -> list[Issue]:
    """subtotal + taxes == total."""
    subtotal, total = money(fields, "subtotal"), money(fields, "total")
    if subtotal is None or total is None:
        return []
    if any(fields.get(k) is not None and fields[k].problem for k in TAX_KEYS):
        return []  # an unreadable tax is already an error; the sum would be meaningless
    tax, _ = _taxes(fields)
    expected = subtotal + tax
    if close(expected, total, cfg.tolerance):
        return []
    return [
        Issue(
            "total_mismatch",
            Sev.ERROR,
            f"Subtotal {fmt(subtotal)} + tax {fmt(tax)} = {fmt(expected)}, "
            f"but total is {fmt(total)} (difference {fmt(total - expected)}).",
            "total",
        )
    ]


def check_line_sum(
    fields: Mapping[str, FieldInput], lines: Sequence[LineInput], cfg: RuleConfig
) -> list[Issue]:
    """Sum of line-item amounts == subtotal (only when every line amount is known)."""
    subtotal = money(fields, "subtotal")
    if subtotal is None or not lines:
        return []
    if any(line.amount is None for line in lines):
        return [
            Issue(
                "line_sum_unverified",
                Sev.WARNING,
                "Some line amounts are missing, so line items cannot be checked against "
                "the subtotal.",
                "subtotal",
            )
        ]
    line_total = sum((line.amount for line in lines if line.amount is not None), ZERO)
    if close(line_total, subtotal, cfg.tolerance):
        return []
    return [
        Issue(
            "line_sum_mismatch",
            Sev.ERROR,
            f"Line items add up to {fmt(line_total)}, but subtotal is {fmt(subtotal)} "
            f"(difference {fmt(subtotal - line_total)}).",
            "subtotal",
        )
    ]


def check_tax_rate(fields: Mapping[str, FieldInput], cfg: RuleConfig) -> list[Issue]:
    """Warn when total tax is far from the expected rate (Ontario HST 13%). Warning only."""
    subtotal = money(fields, "subtotal")
    tax, present = _taxes(fields)
    if subtotal is None or subtotal <= ZERO or not present or tax == ZERO:
        return []
    rate = tax / subtotal
    if abs(rate - cfg.expected_tax_rate) <= cfg.tax_rate_slack:
        return []
    return [
        Issue(
            "tax_rate_unusual",
            Sev.WARNING,
            f"Tax is {rate * 100:.1f}% of the subtotal; "
            f"{cfg.expected_tax_rate * 100:.0f}% was expected. Check the tax amounts.",
            "hst" if money(fields, "hst") is not None else "gst",
        )
    ]


def check_dates(fields: Mapping[str, FieldInput], cfg: RuleConfig) -> list[Issue]:
    invoice_date, due_date = day(fields, "invoice_date"), day(fields, "due_date")
    issues = future_date(invoice_date, "invoice_date", cfg.today)
    if invoice_date and due_date and due_date < invoice_date:
        issues.append(
            Issue("due_before_invoice", Sev.WARNING, "Due date is before invoice date.", "due_date")
        )
    return issues


def check_negative_total(fields: Mapping[str, FieldInput]) -> list[Issue]:
    total = money(fields, "total")
    if total is not None and total < ZERO:
        return [
            Issue(
                "negative_total",
                Sev.WARNING,
                "Total is negative. Confirm this is a refund or credit note.",
                "total",
            )
        ]
    return []


def validate_receipt(
    fields: Mapping[str, FieldInput],
    lines: Sequence[LineInput],
    cfg: RuleConfig,
    *,
    invoice: bool = False,
) -> list[Issue]:
    required = REQUIRED_INVOICE if invoice else REQUIRED_RECEIPT
    return [
        *field_problems(fields, required),
        *line_problems(lines),
        *check_totals(fields, cfg),
        *check_line_sum(fields, lines, cfg),
        *check_tax_rate(fields, cfg),
        *check_dates(fields, cfg),
        *check_negative_total(fields),
    ]
