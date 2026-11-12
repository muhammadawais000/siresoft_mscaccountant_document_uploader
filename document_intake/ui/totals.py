"""The "Totals check" panel, worked out on the server with Decimal.

Each function returns a list of rows that the template prints as they are:
``{"label", "value", "op", "total", "check"}`` where ``check`` is ``None`` for a plain row,
or ``{"ok": bool | None, "difference": Decimal | None}`` for a comparison row.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

from document_intake.conf import intake_settings

MINUS = "\u2212"  # the maths minus sign, shown in the totals table


def to_decimal(text: str | None) -> Decimal | None:
    """A header field value as a Decimal, or None when it is blank or unreadable."""
    cleaned = (text or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _sum(values: Iterable[Decimal | None]) -> Decimal:
    return sum((v for v in values if v is not None), Decimal("0"))


def _row(label: str, value: Decimal | None, op: str = "", total: bool = False) -> dict[str, Any]:
    return {"label": label, "value": value, "op": op, "total": total, "check": None}


def _check(label: str, expected: Decimal | None, stated: Decimal | None) -> dict[str, Any]:
    check: dict[str, Any]
    if expected is None or stated is None:
        check = {"ok": None, "difference": None}
    else:
        difference = stated - expected
        tolerance = Decimal(str(intake_settings.AMOUNT_TOLERANCE))
        check = {"ok": abs(difference) <= tolerance, "difference": difference}
    return {"label": label, "value": None, "op": "", "total": False, "check": check}


def statement_rows(
    fields: dict[str, str], lines: list[Any], credit_card: bool
) -> list[dict[str, Any]]:
    """Opening + movements = closing. Bank: +credits -debits. Card: +charges -payments."""
    opening = to_decimal(fields.get("opening_balance"))
    closing = to_decimal(fields.get("closing_balance"))
    debits = _sum(line.debit for line in lines)
    credits = _sum(line.credit for line in lines)
    movement = debits - credits if credit_card else credits - debits
    computed = None if opening is None else opening + movement
    return [
        _row("Opening balance", opening),
        _row(
            "Payments and credits" if credit_card else "Credits (money in)",
            credits,
            MINUS if credit_card else "+",
        ),
        _row(
            "Purchases and charges" if credit_card else "Debits (money out)",
            debits,
            "+" if credit_card else MINUS,
        ),
        _row("Calculated closing balance", computed, "=", total=True),
        _row("Closing balance on statement", closing),
        _check("Difference", computed, closing),
    ]


def receipt_rows(fields: dict[str, str], lines: list[Any]) -> list[dict[str, Any]]:
    """Line items vs subtotal, and subtotal + taxes vs total."""
    subtotal = to_decimal(fields.get("subtotal"))
    total = to_decimal(fields.get("total"))
    taxes = _sum(to_decimal(fields.get(key)) for key in ("gst", "hst", "pst"))
    line_sum = _sum(line.amount for line in lines) if lines else None
    computed = None if subtotal is None else subtotal + taxes
    return [
        _row("Line items", line_sum),
        _row("Subtotal on document", subtotal),
        _check("Lines vs subtotal", line_sum, subtotal),
        _row("Taxes (GST + HST + PST)", taxes, "+"),
        _row("Calculated total", computed, "=", total=True),
        _row("Total on document", total),
        _check("Difference", computed, total),
    ]
