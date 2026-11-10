"""Turn model output into typed values and review rows.

Principle: never repair a value by guessing. Anything that does not parse cleanly is kept
verbatim (so the reviewer sees what the model said) and flagged as an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from document_intake.models import FieldStatus

from .schemas import AMOUNT_KEYS, DATE_KEYS, HEADER_FIELDS, family

CENT = Decimal("0.01")
_CURRENCY_TOKENS = re.compile(r"(?i)\b(CAD|USD|C\$|US\$)\b|\$")
_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


class ParseError(ValueError):
    """A value is present but not in an acceptable form."""


def parse_money(value: Any) -> Decimal | None:
    """Parse an amount to a Decimal with 2 places.

    Accepts "1234.56", "1,234.56", "$12.00", "(12.00)" and "12.00-" (negatives).
    Returns None for null/blank. Raises ParseError for anything else, including values
    with more than 2 non-zero decimal places (never silently rounded).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ParseError("not an amount")
    if isinstance(value, int):
        return Decimal(value).quantize(CENT)
    if isinstance(value, float):
        # The schema asks for strings; a float has already lost precision.
        raise ParseError("amount was not returned as a decimal string")
    text = str(value).strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    if text.endswith("-"):
        negative, text = True, text[:-1].strip()
    text = _CURRENCY_TOKENS.sub("", text).replace(" ", "")
    if text.startswith("-") and negative:
        raise ParseError("ambiguous sign")
    if _THOUSANDS.match(text):
        text = text.replace(",", "")
    if not _PLAIN_NUMBER.match(text):
        raise ParseError("unreadable amount")

    try:
        amount = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards
        raise ParseError("unreadable amount") from exc
    if amount != amount.quantize(CENT):
        raise ParseError("more than 2 decimal places")
    amount = amount.quantize(CENT)
    return -amount if negative else amount


def parse_iso_date(value: Any) -> date | None:
    """Strict YYYY-MM-DD. Returns None for null/blank; raises ParseError otherwise."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip()
    if not _ISO_DATE.match(text):
        raise ParseError("date is not YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ParseError("not a real calendar date") from exc


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


@dataclass
class FieldRow:
    key: str
    label: str
    position: int
    value: str
    normalized_value: str
    status: str
    status_reason: str = ""


@dataclass
class LineRow:
    line_no: int
    raw: dict[str, Any]
    date: date | None = None
    description: str = ""
    debit: Decimal | None = None
    credit: Decimal | None = None
    amount: Decimal | None = None
    balance: Decimal | None = None
    tax: Decimal | None = None
    status: str = FieldStatus.OK
    reasons: list[str] = field(default_factory=list)

    def flag(self, status: str, reason: str) -> None:
        rank: dict[str, int] = {FieldStatus.OK: 0, FieldStatus.WARNING: 1, FieldStatus.MISSING: 2}
        if rank.get(status, 3) > rank.get(self.status, 3):
            self.status = status
        self.reasons.append(reason)

    @property
    def status_reason(self) -> str:
        return "; ".join(self.reasons)[:255]


def _header_row(key: str, label: str, position: int, raw: Any, currency: str) -> FieldRow:
    value = "" if raw is None else str(raw).strip()
    row = FieldRow(key, label, position, value, "", FieldStatus.OK)

    if key == "currency":
        if _blank(raw):
            row.normalized_value = currency
            row.status, row.status_reason = FieldStatus.WARNING, f"Not shown; assumed {currency}"
        elif _CURRENCY_CODE.match(value.upper()):
            row.normalized_value = value.upper()
        else:
            row.status, row.status_reason = FieldStatus.ERROR, "Not an ISO currency code"
        return row

    if _blank(raw):
        row.status, row.status_reason = FieldStatus.MISSING, "Not found or not legible"
        return row

    if key == "account_last4":
        digits = re.sub(r"\D", "", value)
        row.value = digits[-4:]
        if len(digits) < 4:
            row.status, row.status_reason = FieldStatus.ERROR, "Fewer than 4 digits"
        elif len(digits) > 4:
            # Never store full account numbers; keeping the last 4 is masking, not guessing.
            row.status, row.status_reason = FieldStatus.WARNING, "Full number masked to last 4"
        row.normalized_value = row.value
        return row

    try:
        if key in AMOUNT_KEYS:
            row.normalized_value = str(parse_money(raw))
        elif key in DATE_KEYS:
            parsed = parse_iso_date(raw)
            row.normalized_value = parsed.isoformat() if parsed else ""
        else:
            row.normalized_value = value
    except ParseError as exc:
        row.status, row.status_reason = FieldStatus.ERROR, str(exc).capitalize()
    return row


def build_fields(doc_type: str, data: dict[str, Any], default_currency: str) -> list[FieldRow]:
    """Header rows for the review form, in display order."""
    return [
        _header_row(key, label, position, data.get(key), default_currency)
        for position, (key, label) in enumerate(HEADER_FIELDS[family(doc_type)])
    ]


def _money_into(line: LineRow, item: dict[str, Any], key: str) -> Decimal | None:
    try:
        return parse_money(item.get(key))
    except ParseError as exc:
        line.flag(FieldStatus.ERROR, f"{key}: {exc}")
        return None


def _statement_line(line_no: int, item: dict[str, Any]) -> LineRow:
    line = LineRow(line_no=line_no, raw=item, description=str(item.get("description") or ""))
    try:
        line.date = parse_iso_date(item.get("date"))
    except ParseError as exc:
        line.flag(FieldStatus.ERROR, f"date: {exc}")
    else:
        if line.date is None:
            line.flag(FieldStatus.MISSING, "date missing")

    line.debit = _money_into(line, item, "debit")
    line.credit = _money_into(line, item, "credit")
    line.balance = _money_into(line, item, "balance")
    for column in ("debit", "credit"):
        if (amount := getattr(line, column)) is not None and amount < 0:
            setattr(line, column, None)
            line.flag(FieldStatus.ERROR, f"{column}: negative value in a {column} column")

    if line.debit is None and line.credit is None:
        if not any(r.startswith(("debit", "credit")) for r in line.reasons):
            line.flag(FieldStatus.MISSING, "no debit or credit amount")
    elif line.debit and line.credit:
        # A 0.00 in the other column is how many statements print "nothing".
        line.flag(FieldStatus.WARNING, "both debit and credit present")
    # Signed amount: money in is positive, money out negative. Pure arithmetic on read values.
    if line.debit is not None or line.credit is not None:
        line.amount = (line.credit or Decimal("0")) - (line.debit or Decimal("0"))
    if not line.description.strip():
        line.flag(FieldStatus.WARNING, "description missing")
    return line


def _item_line(line_no: int, item: dict[str, Any]) -> LineRow:
    line = LineRow(line_no=line_no, raw=item, description=str(item.get("description") or ""))
    line.amount = _money_into(line, item, "amount")
    line.tax = _money_into(line, item, "tax")
    if line.amount is None and not line.reasons:
        line.flag(FieldStatus.MISSING, "amount missing")
    if not line.description.strip():
        line.flag(FieldStatus.WARNING, "description missing")
    return line


def build_lines(doc_type: str, data: dict[str, Any]) -> list[LineRow]:
    """Transaction rows (statements) or line-item rows (receipts / invoices)."""
    if family(doc_type) == "statement":
        items = data.get("transactions") or []
        return [_statement_line(i, item) for i, item in enumerate(items, 1)]
    items = data.get("line_items") or []
    return [_item_line(i, item) for i, item in enumerate(items, 1)]
