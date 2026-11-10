"""Turn model output (or reviewer edits) into typed values.

Principle: never repair a value by guessing. Anything that does not parse cleanly is kept
verbatim (so the reviewer sees what the model said) and reported as a problem. Statuses
are not decided here; the validation engine derives them from these results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from document_intake.validators.types import FieldInput, LineInput, Sev

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


# --------------------------------------------------------------- header fields


@dataclass(frozen=True)
class HeaderValue:
    """One header value: what to store, and what validation needs to know."""

    value: str
    normalized_value: str
    parsed: Decimal | date | str | None
    problem: str = ""
    warning: str = ""

    def as_input(self, key: str) -> FieldInput:
        return FieldInput(key, self.value, self.parsed, self.problem, self.warning)


def parse_header(key: str, raw: Any, default_currency: str) -> HeaderValue:
    """Parse one header value from the model or from a reviewer's edit."""
    value = "" if raw is None else str(raw).strip()

    if key == "currency":
        if not value:
            return HeaderValue(
                "",
                default_currency,
                default_currency,
                warning=f"Currency not shown; assumed {default_currency}.",
            )
        if _CURRENCY_CODE.match(value.upper()):
            return HeaderValue(value, value.upper(), value.upper())
        return HeaderValue(value, "", None, problem="not an ISO currency code")

    if not value:
        return HeaderValue("", "", None)

    if key == "account_last4":
        digits = re.sub(r"\D", "", value)
        if len(digits) < 4:
            return HeaderValue(value, "", None, problem="fewer than 4 digits")
        masked = digits[-4:]
        # Never store full account numbers; keeping the last 4 is masking, not guessing.
        warning = "Full number was masked to the last 4 digits." if len(digits) > 4 else ""
        return HeaderValue(masked, masked, masked, warning=warning)

    try:
        if key in AMOUNT_KEYS:
            amount = parse_money(value)
            return HeaderValue(value, str(amount), amount)
        if key in DATE_KEYS:
            parsed = parse_iso_date(value)
            return HeaderValue(value, parsed.isoformat() if parsed else "", parsed)
    except ParseError as exc:
        return HeaderValue(value, "", None, problem=str(exc))
    return HeaderValue(value, value, value)


@dataclass(frozen=True)
class FieldRow:
    key: str
    label: str
    position: int
    value: HeaderValue


def build_fields(doc_type: str, data: dict[str, Any], default_currency: str) -> list[FieldRow]:
    """Header rows for the review form, in display order."""
    return [
        FieldRow(key, label, position, parse_header(key, data.get(key), default_currency))
        for position, (key, label) in enumerate(HEADER_FIELDS[family(doc_type)])
    ]


# ------------------------------------------------------------------------ lines


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
    problems: list[tuple[Sev, str]] = field(default_factory=list)

    def as_input(self) -> LineInput:
        return LineInput(
            line_no=self.line_no,
            date=self.date,
            description=self.description,
            debit=self.debit,
            credit=self.credit,
            amount=self.amount,
            balance=self.balance,
            tax=self.tax,
            problems=tuple(self.problems),
        )


def _money_into(line: LineRow, item: dict[str, Any], key: str) -> Decimal | None:
    try:
        return parse_money(item.get(key))
    except ParseError as exc:
        line.problems.append((Sev.ERROR, f"{key.capitalize()}: {exc}."))
        return None


def statement_line_problems(line: LineRow) -> list[tuple[Sev, str]]:
    """Checks that only need the typed values (shared by parsing and re-validation)."""
    problems: list[tuple[Sev, str]] = []
    if line.date is None:
        problems.append((Sev.WARNING, "Date is missing."))
    if line.debit is None and line.credit is None:
        problems.append((Sev.ERROR, "No debit or credit amount."))
    elif line.debit and line.credit:
        # A 0.00 in the other column is how many statements print "nothing".
        problems.append((Sev.WARNING, "Both debit and credit are filled in."))
    if not line.description.strip():
        problems.append((Sev.WARNING, "Description is missing."))
    return problems


def item_line_problems(line: LineRow) -> list[tuple[Sev, str]]:
    problems: list[tuple[Sev, str]] = []
    if line.amount is None:
        problems.append((Sev.ERROR, "Amount is missing."))
    if not line.description.strip():
        problems.append((Sev.WARNING, "Description is missing."))
    return problems


def _statement_line(line_no: int, item: dict[str, Any]) -> LineRow:
    line = LineRow(line_no=line_no, raw=item, description=str(item.get("description") or ""))
    unreadable = False
    try:
        line.date = parse_iso_date(item.get("date"))
    except ParseError as exc:
        line.problems.append((Sev.ERROR, f"Date: {exc}."))
        unreadable = True
    count = len(line.problems)
    line.debit = _money_into(line, item, "debit")
    line.credit = _money_into(line, item, "credit")
    line.balance = _money_into(line, item, "balance")
    unreadable = unreadable or len(line.problems) > count
    for column in ("debit", "credit"):
        if (amount := getattr(line, column)) is not None and amount < 0:
            setattr(line, column, None)
            line.problems.append((Sev.ERROR, f"{column.capitalize()}: negative value."))
            unreadable = True
    # Signed amount: money in is positive, money out negative. Pure arithmetic on read values.
    if line.debit is not None or line.credit is not None:
        line.amount = (line.credit or Decimal("0")) - (line.debit or Decimal("0"))
    for severity, message in statement_line_problems(line):
        # Do not repeat "missing" when the value was present but unreadable.
        if unreadable and message in ("Date is missing.", "No debit or credit amount."):
            continue
        line.problems.append((severity, message))
    return line


def _item_line(line_no: int, item: dict[str, Any]) -> LineRow:
    line = LineRow(line_no=line_no, raw=item, description=str(item.get("description") or ""))
    line.amount = _money_into(line, item, "amount")
    line.tax = _money_into(line, item, "tax")
    unreadable = bool(line.problems)
    for severity, message in item_line_problems(line):
        if unreadable and message == "Amount is missing.":
            continue
        line.problems.append((severity, message))
    return line


def build_lines(doc_type: str, data: dict[str, Any]) -> list[LineRow]:
    """Transaction rows (statements) or line-item rows (receipts / invoices)."""
    if family(doc_type) == "statement":
        items = data.get("transactions") or []
        return [_statement_line(i, item) for i, item in enumerate(items, 1)]
    items = data.get("line_items") or []
    return [_item_line(i, item) for i, item in enumerate(items, 1)]
