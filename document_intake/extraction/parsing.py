"""Turn model output (or reviewer edits) into typed values.

Principle: never repair a value by guessing. Anything that does not parse cleanly is kept
verbatim (so the reviewer sees what the model said) and reported as a problem. Statuses
are not decided here; the validation engine derives them from these results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from datetime import date as Date  # noqa: N812 - a field below is called "date"
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


_TAX_NOTE = re.compile(r"\s*\([A-Za-z]\)$")
_CR_SUFFIX = re.compile(r"(?i)\s*CR$")


def parse_printed_amount(value: Any) -> Decimal | None:
    """An amount exactly as a card statement prints it; the sign comes only from the text.

    Accepts "-$2,000.00", "$ -613.03", "1.72 (e)" (tax footnote) and "12.00 CR" (credit).
    """
    if not isinstance(value, str):
        return parse_money(value)
    text = _TAX_NOTE.sub("", value.strip().replace("\u2212", "-").replace("\u2013", "-"))
    if _CR_SUFFIX.search(text):
        amount = parse_money(_CR_SUFFIX.sub("", text))
        if amount is not None and amount < 0:
            raise ParseError("ambiguous sign")
        return None if amount is None else -amount
    return parse_money(text)


_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MONTH_DAY = re.compile(r"^([A-Z]{3,9})\.?\s*(\d{1,2})(?:\s*,?\s*(\d{4}))?$")
_DAY_MONTH = re.compile(r"^(\d{1,2})\s*([A-Z]{3,9})\.?(?:\s*,?\s*(\d{4}))?$")
_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})$")


def parse_printed_date(value: Any, reference: date | None) -> date | None:
    """A statement row date ("12/03", "OCT6", "Oct 6, 2025"). Returns None for blank.

    A missing year comes from ``reference`` (the statement date): months after the
    statement's month belong to the year before. Without a reference it is not guessed.
    """
    text = "" if value is None else str(value).strip().upper()
    if not text:
        return None
    if _ISO_DATE.match(text):
        parsed = parse_iso_date(text)
        # Rows rarely print a year, so a year given by the model must fit the statement.
        if parsed is None or reference is None:
            return parsed
        return _with_year(parsed.month, parsed.day, None, reference)
    if match := _SLASH.match(text):
        return _with_year(int(match[1]), int(match[2]), None, reference)
    if match := _MONTH_DAY.match(text):
        name, day, year = match[1], match[2], match[3]
    elif match := _DAY_MONTH.match(text):
        day, name, year = match[1], match[2], match[3]
    else:
        raise ParseError("unreadable date")
    if name[:3] not in _MONTHS:
        raise ParseError("unreadable date")
    month = _MONTHS.index(name[:3]) + 1
    return _with_year(month, int(day), int(year) if year else None, reference)


def _with_year(month: int, day: int, year: int | None, reference: date | None) -> date:
    if year is None:
        if reference is None:
            raise ParseError("the year is not known")
        year = reference.year - 1 if month > reference.month else reference.year
    try:
        return date(year, month, day)
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
    # Credit card statements only.
    posting_date: Date | None = None
    transaction_number: str = ""
    auth_number: str = ""
    card_last4: str = ""
    page: int | None = None
    pre_tax: Decimal | None = None
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
            posting_date=self.posting_date,
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


_CREDIT_WORDS = re.compile(r"(?i)\b(PYMT|PAYMENT|CASHBACK|REMB(?![A-Z])|REFUND|CREDIT)")


def looks_like_credit(description: str) -> bool:
    return bool(_CREDIT_WORDS.search(description))


def card_line_problems(line: LineRow) -> list[tuple[Sev, str]]:
    """Checks on one card transaction. The wording never changes debit or credit."""
    problems: list[tuple[Sev, str]] = []
    if line.date is None:
        problems.append((Sev.WARNING, "Date is missing."))
    if line.debit is None and line.credit is None:
        problems.append((Sev.ERROR, "Amount is missing."))
    elif line.debit and line.credit:
        problems.append((Sev.WARNING, "Both debit and credit are filled in."))
    elif line.debit and looks_like_credit(line.description):
        problems.append(
            (Sev.WARNING, "Reads like a payment or refund, but the printed amount is a charge.")
        )
    if not line.description.strip():
        problems.append((Sev.WARNING, "Description is missing."))
    return problems


def set_signed_amount(line: LineRow, amount: Decimal | None) -> None:
    """Printed sign -> columns. ``amount`` keeps its older meaning: credit minus debit."""
    if amount is None:
        return
    line.debit = amount if amount >= 0 else None
    line.credit = -amount if amount < 0 else None
    line.amount = -amount


def _card_line(line_no: int, item: dict[str, Any]) -> LineRow:
    """One row of a card statement as printed (see ``statement_processor``)."""
    line = LineRow(
        line_no=line_no,
        raw=item,
        description=str(item.get("description") or ""),
        transaction_number=str(item.get("transaction_number") or "")[:64],
        auth_number=str(item.get("auth_number") or "")[:64],
        card_last4=str(item.get("card_last4") or "")[-4:],
        page=item.get("page"),
    )
    reference = parse_iso_date(item.get("date_reference"))
    unreadable: set[str] = set()

    def read(key: str, label: str, parse: Any, missing: str = "") -> Any:
        try:
            return parse(item.get(key))
        except ParseError as exc:
            line.problems.append((Sev.ERROR, f"{label}: {exc}."))
            unreadable.add(missing)
            return None

    line.date = read(
        "trans_date", "Date", lambda v: parse_printed_date(v, reference), "Date is missing."
    )
    line.posting_date = read(
        "posting_date", "Posting date", lambda v: parse_printed_date(v, reference)
    )
    line.pre_tax = read("pre_tax_amount", "Pre-tax", parse_printed_amount)
    line.tax = read("tax_amount", "Tax", parse_printed_amount)
    # The exact printed text decides; the model's own "amount" is only a cross-check.
    printed = read("amount_raw", "Amount", parse_printed_amount, "Amount is missing.")
    if not item.get("amount_raw"):
        printed = read("amount", "Amount", parse_printed_amount, "Amount is missing.")
    elif printed is not None:
        try:
            other = parse_printed_amount(item.get("amount"))
        except ParseError:
            other = None
        if other is not None and other != printed:
            line.problems.append(
                (Sev.WARNING, f"Read as {other} but printed {item['amount_raw']}; printed used.")
            )
    set_signed_amount(line, printed)
    line.problems.extend(p for p in card_line_problems(line) if p[1] not in unreadable)
    return line


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
        # Card rows carry "amount_raw"; results from before that format keep the old parser.
        return [
            _card_line(i, item) if "amount_raw" in item else _statement_line(i, item)
            for i, item in enumerate(items, 1)
        ]
    items = data.get("line_items") or []
    return [_item_line(i, item) for i, item in enumerate(items, 1)]
