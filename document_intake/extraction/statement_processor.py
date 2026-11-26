"""Credit card statements: everything that is decided by code and not by the model.

The model only copies what is printed. This module (pure Python, no Django, no model calls)
reads the sections and row count from the PDF text, merges the pages, splits debits from
credits by the printed sign, adds up, and checks the result against the statement.
All money is ``Decimal``.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .parsing import ParseError, parse_iso_date, parse_printed_amount, parse_printed_date

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

# ------------------------------------------------------------- what the model returns


class _Printed(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class PrintedSummary(_Printed):
    institution: str | None = None
    card_last4: str | None = None
    currency: str | None = None
    statement_date: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    payment_due_date: str | None = None
    previous_balance: str | None = None
    new_balance: str | None = None
    payments: str | None = None
    payments_and_credits: str | None = None
    purchases_and_other_charges: str | None = None
    net_purchases: str | None = None
    cash_advances: str | None = None
    interest: str | None = None
    fees: str | None = None
    adjustments: str | None = None
    other_charges: str | None = None


class PrintedSection(_Printed):
    card_last4: str | None = None
    total_credits: str | None = None
    total_debits: str | None = None


class PrintedTransaction(_Printed):
    trans_date: str | None = None
    posting_date: str | None = None
    transaction_number: str | None = None
    auth_number: str | None = None
    description: str | None = None
    card_last4: str | None = None
    pre_tax_amount: str | None = None
    tax_amount: str | None = None
    amount: str | None = None
    amount_raw: str | None = None


class PrintedPage(_Printed):
    """One page of a card statement, exactly as printed."""

    summary: PrintedSummary
    section_totals: list[PrintedSection]
    transactions: list[PrintedTransaction]
    rows_detected_in_text: int | None = None
    rows_returned: int | None = None


# ---------------------------------------------------------------------- PDF text

_PAGE_MARK = re.compile(r"^--- Page \d+ ---$", re.M)
_MONTH = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?"
# Two dates at the start of a line. pdfplumber often drops the spaces ("OCT6 OCT7").
_ROW_PATTERNS = (
    re.compile(r"^\d{2}/\d{2}\s+\d{2}/\d{2}\b", re.M),  # BMO: 12/03 12/04
    re.compile(rf"^{_MONTH}\s?\d{{1,2}}\s*{_MONTH}\s?\d{{1,2}}\b", re.M | re.I),  # TD, CIBC
)
# Looser: any line that starts with a date and holds an amount. Such a page is never skipped.
_DATED_AMOUNT = re.compile(
    rf"^(?:{_MONTH}\s?\d{{1,2}}|\d{{1,2}}\s?{_MONTH}|\d{{1,2}}/\d{{1,2}}|\d{{4}}-\d{{2}}-\d{{2}})"
    r"(?!\d).*\d\.\d{2}",
    re.M | re.I,
)
# The masked number only: a holder name can start with digits too ("1234567 ONTARIO INC").
_CARD_HEADER = re.compile(
    r"^Card\s*Number\s*:?\s*(?:[\dX*]{4}[\s-]+[\dX*]{4}[\s-]+[\dX*]{4}[\s-]+|[X*\s.-]+)(\d{4})(?!\d)",
    re.I,
)
_CARD_FOOTER = re.compile(
    r"^TOTAL\s*(CREDITS|DEBITS)\s+[X*\d\-\s]*?(\d{4})\s+(\$?\s*-?\s*\$?[\d,]+\.\d{2})", re.I
)
_SKIP_WORDS = re.compile(
    r"customer\s*service|telephone\s*inquir|lost/?stolen|payment\s*information|"
    r"mail\s*your\s*payment|terms\s*and\s*conditions|important\s*information|cardholder\s*agreement|"
    r"interest\s*is\s*calculated|privacy|registered\s*trade|foreign\s*currency\s*conversion",
    re.I,
)
_SUMMARY_WORDS = re.compile(
    r"previous\s*(?:statement\s*)?balance|new\s*(?:account\s*)?balance", re.I
)


# An amount with its own sign: "$ 14.98", "-$2,000.00", "$ -613.03", "-18.00", "12.00 CR".
_AMOUNT_TOKEN = re.compile(
    r"(?<![\d.,])-?\$?\s?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?!\d)(?:\s?CR\b)?", re.I
)


@dataclass(frozen=True)
class TextRow:
    page: int
    line: str
    card_last4: str  # "" when no card header was printed above it
    next_line: str = ""  # often the Trans ID and Auth # of this row

    @property
    def amounts(self) -> list[Decimal]:
        """Every amount on the line, signed as printed."""
        values = []
        for token in _AMOUNT_TOKEN.findall(self.line):
            try:
                value = parse_printed_amount(token)
            except ParseError:
                continue
            if value is not None:
                values.append(value)
        return values

    def starts_with_dates(self, item: PrintedTransaction) -> bool:
        dates = re.sub(r"\s", "", f"{item.trans_date or ''}{item.posting_date or ''}").upper()
        return bool(dates) and re.sub(r"\s", "", self.line).upper().startswith(dates)


@dataclass(frozen=True)
class TextLayout:
    rows: tuple[TextRow, ...]
    footers: dict[str, dict[str, Decimal]]
    first_card: dict[int, str]  # page -> card in progress where the page starts
    has_sections: bool


def page_body(page: str) -> str:
    """Page text without the repeated pipe-separated tables."""
    return page.split("[Tables]")[0].strip()


def split_pages(raw_text: str) -> list[str]:
    """Page bodies of a stored ``raw_text`` ("--- Page n ---" markers)."""
    if not raw_text.strip():
        return []
    return [page_body(part) for part in _PAGE_MARK.split(raw_text)[1:]]


def _row_pattern(pages: Sequence[str]) -> re.Pattern[str] | None:
    """The pattern this statement uses (the one that matches most lines)."""
    counts = [sum(len(p.findall(page)) for page in pages) for p in _ROW_PATTERNS]
    best = max(range(len(counts)), key=lambda i: counts[i])
    return _ROW_PATTERNS[best] if counts[best] else None


def count_rows(pages: Sequence[str]) -> int | None:
    """Transaction lines in the PDF text; None when the layout is not recognised."""
    pattern = _row_pattern(pages)
    return None if pattern is None else sum(len(pattern.findall(page)) for page in pages)


def read_layout(pages: Sequence[str]) -> TextLayout:
    """Which card each printed row belongs to, and each card's printed totals.

    Sections run across pages: a page can start in the middle of a card, and a card's totals
    can stand alone on the next page. So this reads the whole document in order.
    """
    pattern = _row_pattern(pages)
    card, found = "", False
    rows: list[TextRow] = []
    footers: dict[str, dict[str, Decimal]] = {}
    first: dict[int, str] = {}
    for number, page in enumerate(pages, 1):
        first[number] = card
        lines = page.splitlines()
        for position, line in enumerate(lines):
            if header := _CARD_HEADER.match(line):
                card, found = header[1], True
            elif footer := _CARD_FOOTER.match(line):
                key = footer[1].lower()
                footers.setdefault(footer[2], {})[key] = abs(
                    parse_printed_amount(footer[3]) or ZERO
                )
            elif pattern is not None and pattern.match(line):
                following = lines[position + 1] if position + 1 < len(lines) else ""
                rows.append(TextRow(number, line, card, following))
    return TextLayout(tuple(rows), footers, first, found)


def needs_model(page: str, number: int) -> bool:
    """False for pages that clearly hold no transactions. Dated amounts always win."""
    if number == 1 or _DATED_AMOUNT.search(page) or _SUMMARY_WORDS.search(page):
        return True
    if any(pattern.search(page) for pattern in _ROW_PATTERNS):
        return True
    lines = [line for line in page.splitlines() if line.strip()]
    footers_only = all(_CARD_FOOTER.match(x) or re.match(r"^Page \d+", x, re.I) for x in lines)
    return not (footers_only or _SKIP_WORDS.search(page))


# ------------------------------------------------------------------- post-processing

_ALWAYS_SUMMARY = re.compile(
    r"(?:PREVIOUS(?:STATEMENT|ACCOUNT)?BALANCE|TOTALNEWBALANCE|NEWACCOUNTBALANCE|"
    r"TOTAL(?:CREDITS|DEBITS)[X*\d]*|SUBTOTAL|TOTALREWARDSEARNED|REWARDS(?:EARNED|ADJUSTED)|"
    r"REWARDSBALANCE(?:YEARTODATE)?)"
)
# A shop can be called "New Balance", so this one is a summary line only without a date.
_UNDATED_SUMMARY = re.compile(r"(?:NEWBALANCE|TOTAL|TOTALBALANCE)")


def is_non_transaction(description: str, *, dated: bool) -> bool:
    """Summary lines such as PREVIOUS STATEMENT BALANCE. The whole description must match."""
    text = re.sub(r"[^A-Z0-9*]", "", description.upper())
    if _ALWAYS_SUMMARY.fullmatch(text):
        return True
    return not dated and bool(_UNDATED_SUMMARY.fullmatch(text))


def last4(value: str | None) -> str:
    """The last 4 digits; "" when there are fewer. Full numbers are never kept."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-4:] if len(digits) >= 4 else ""


def _header_date(value: str | None, text: str) -> str | None:
    """ISO form of a header date when it is unambiguous; otherwise the printed text."""
    if value is None or not value.strip():
        return None
    try:
        parsed = parse_iso_date(value)
        return parsed.isoformat() if parsed else None
    except ParseError:
        pass
    if match := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value.strip()):
        a, b, year = (int(part) for part in match.groups())
        day_first = a > 12 or "(DD/MM/YYYY)" in text
        if day_first or b > 12 or "(MM/DD/YYYY)" in text:
            month, day = (b, a) if day_first else (a, b)
            try:
                return dt.date(year, month, day).isoformat()
            except ValueError:
                return value
        return value
    try:
        parsed = parse_printed_date(value, None)
    except ParseError:
        return value
    return parsed.isoformat() if parsed else value


def _plain_amount(value: str | None) -> str | None:
    """$7,296.26 -> 7296.26; unreadable text is kept as printed."""
    try:
        amount = parse_printed_amount(value)
    except ParseError:
        return value
    return None if amount is None else str(amount)


def _model_amount(item: PrintedTransaction) -> Decimal | None:
    try:
        return parse_printed_amount(item.amount_raw or item.amount)
    except ParseError:
        return None


def _find_text_row(
    item: PrintedTransaction, page: int, layout: TextLayout, used: set[int]
) -> int | None:
    """The printed line this row was copied from: same size of amount, first unused line.

    The sign is ignored here on purpose, because the model's sign is what we verify. A line
    with the same dates is preferred over an equal amount elsewhere on the page.
    """
    amount = _model_amount(item)
    if amount is None:
        # No amount to compare: tie it to its line by the dates, so that line is not asked for
        # again. The amount itself is not filled in from the line; the row stays flagged.
        same_dates = [
            i
            for i, row in enumerate(layout.rows)
            if row.page == page and i not in used and row.starts_with_dates(item)
        ]
        return next(iter(same_dates), None)
    matches = [
        i
        for i, row in enumerate(layout.rows)
        if row.page == page
        and i not in used
        and any(abs(value) == abs(amount) for value in row.amounts)
    ]
    dated = [i for i in matches if layout.rows[i].starts_with_dates(item)]
    return next(iter(dated or matches), None)


def _apply_text(
    row: dict[str, Any], item: PrintedTransaction, text_row: TextRow | None, has_rows: bool
) -> None:
    """Take the signed amount from the PDF line itself; note when the model's sign differed."""
    amount = _model_amount(item)
    row["text_amount"] = None
    row["sign_corrected"] = False
    row["not_in_text"] = has_rows and amount is not None and text_row is None
    if text_row is None or amount is None:
        return
    if not any(abs(value) == abs(amount) for value in text_row.amounts):
        return
    printed = next(value for value in text_row.amounts if abs(value) == abs(amount))
    row["text_amount"] = str(printed)
    row["sign_corrected"] = printed != amount
    # A tax column prints "pre-tax  tax  total" and the three add up.
    values = text_row.amounts
    if len(values) >= 3 and values[-3] + values[-2] == values[-1] == printed:
        row["text_pre_tax"], row["text_tax"] = str(values[-3]), str(values[-2])


def _card_for(
    item: PrintedTransaction, page: int, layout: TextLayout, text_row: TextRow | None, default: str
) -> str:
    """The card a row belongs to: from the printed sections, else from the model."""
    if not layout.has_sections:
        return last4(item.card_last4) or default
    if text_row is not None:
        return text_row.card_last4
    on_page = [row for row in layout.rows if row.page == page]
    for candidate in on_page:
        if candidate.starts_with_dates(item):
            return candidate.card_last4
    cards = {candidate.card_last4 for candidate in on_page}
    return cards.pop() if len(cards) == 1 else last4(item.card_last4)


# ------------------------------------------------------- summary read from the PDF text

_AMT = r"([-+]?\s?\$?\s?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})(?!\d|\s*%)"
_SEP = r"\s*[:=]?\s*"
# Label -> summary key. Spaces are optional because pdfplumber often drops them.
_SUMMARY_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (key, re.compile(f"(?:{pattern})" + _SEP + _AMT, re.I))
    for key, pattern in (
        ("previous_balance", r"previous\s*(?:statement\s*|account\s*)?balance"),
        ("new_balance", r"(?<![a-z])(?:total\s*)?new\s*(?:account\s*)?balance|^total\s*balance"),
        ("payments_and_credits", r"payments\s*(?:&|and)\s*credits|^total\s*credits"),
        ("payments", r"(?<![a-z])payments(?!\s*(?:&|and))"),
        ("purchases_and_other_charges", r"purchases\s*(?:&|and)\s*other\s*charges"),
        ("net_purchases", r"net\s*purchases|(?<![a-z&])purchases(?!\s*(?:&|and))"),
        ("cash_advances", r"cash\s*advances?"),
        ("interest", r"(?<![a-z])interest(?!\s*rate)"),
        ("fees", r"(?<![a-z])fees"),
        ("adjustments", r"(?<![a-z&])adjustments"),
        ("other_charges", r"(?<![a-z&])other\s*charges"),
    )
)
_DATE_TEXT = r"(\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]{3,9}\.?\s?\d{1,2},?\s?\d{4})"
_FORMAT = r"\s*(?:\((?:MM/DD/YYYY|DD/MM/YYYY)\))?"
_DATE_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("statement_date", re.compile(r"statement\s*date" + _FORMAT + _SEP + _DATE_TEXT, re.I)),
    ("payment_due_date", re.compile(r"payment\s*due\s*date" + _FORMAT + _SEP + _DATE_TEXT, re.I)),
)
_PERIOD = re.compile(_DATE_TEXT + r"\s*(?:to|-)\s*" + _DATE_TEXT, re.I)


def read_summary(pages: Sequence[str]) -> dict[str, str]:
    """Summary values printed next to their label (first page that has them).

    These come from the PDF text, so they win over the model's copy. A label that is not
    found leaves the model's value in place.
    """
    found: dict[str, str] = {}
    for page in pages:
        for line in page.splitlines():
            for key, pattern in _SUMMARY_LABELS:
                if key not in found and (match := pattern.search(line)):
                    found[key] = match[1].replace(" ", "")
            for key, pattern in _DATE_LABELS:
                if key not in found and (match := pattern.search(line)):
                    found[key] = match[1]
            if "period_start" not in found and (period := _PERIOD.search(line)):
                found["period_start"], found["period_end"] = period[1], period[2]
    return found


def printed_amounts(pages: Sequence[str]) -> set[Decimal]:
    """Every amount printed anywhere in the PDF text, without its sign."""
    return {abs(value) for page in pages for value in TextRow(0, page, "").amounts}


def merge_pages(answers: Sequence[tuple[int, PrintedPage]], pages: Sequence[str]) -> dict[str, Any]:
    """One statement from the per-page answers, in the shape the rest of the app stores."""
    layout = read_layout(pages)
    text = "\n".join(pages)
    summary: dict[str, str | None] = dict.fromkeys(PrintedSummary.model_fields)
    for _, answer in answers:
        for key, value in answer.summary.model_dump().items():
            if summary[key] is None and value is not None and value.strip():
                summary[key] = value
    from_text = read_summary(pages)
    summary.update(from_text)
    for key in ("statement_date", "period_start", "period_end", "payment_due_date"):
        summary[key] = _header_date(summary[key], text)
    # The statement covers activity up to its statement date. The start is never guessed.
    if summary["period_end"] is None and summary["period_start"] is None:
        summary["period_end"] = summary["statement_date"]
    summary["card_last4"] = last4(summary["card_last4"]) or None

    reference: str | None = None
    for candidate in (summary["statement_date"], summary["period_end"]):
        try:
            parsed = parse_iso_date(candidate)
        except ParseError:
            continue
        if parsed is not None:
            reference = parsed.isoformat()
            break

    transactions: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    used: set[int] = set()
    order: list[float] = []
    for page, answer in answers:
        last = -1.0
        for item in answer.transactions:
            row = {**item.model_dump(), "page": page, "date_reference": reference}
            dated = bool(item.trans_date or item.posting_date)
            if is_non_transaction(item.description or "", dated=dated):
                removed.append(row)
                continue
            index = _find_text_row(item, page, layout, used)
            text_row = None
            if index is not None:
                used.add(index)
                text_row = layout.rows[index]
                last = float(index)
            row["text_index"] = index
            _apply_text(row, item, text_row, bool(layout.rows))
            row["card_last4"] = _card_for(item, page, layout, text_row, summary["card_last4"] or "")
            transactions.append(row)
            # Printed order. A row not found in the text stays right after the one before it.
            order.append(float(index) if index is not None else last + 0.5)
    transactions = [
        row
        for _, _, row in sorted(
            zip(order, range(len(order)), transactions, strict=True),
            key=lambda item: (item[2]["page"], item[0], item[1]),
        )
    ]
    missed = [
        {
            "page": row.page,
            "line": row.line,
            "next_line": row.next_line,
            "card_last4": row.card_last4,
        }
        for i, row in enumerate(layout.rows)
        if i not in used
    ]

    if layout.footers:
        sections = [
            {
                "card_last4": card,
                "total_credits": str(totals["credits"]) if "credits" in totals else None,
                "total_debits": str(totals["debits"]) if "debits" in totals else None,
            }
            for card, totals in layout.footers.items()
        ]
    else:
        sections = [
            {**section.model_dump(), "card_last4": last4(section.card_last4)}
            for _, answer in answers
            for section in answer.section_totals
        ]
    return {
        "institution_name": summary["institution"],
        "account_last4": summary["card_last4"],
        "statement_period_start": summary["period_start"],
        "statement_period_end": summary["period_end"],
        "statement_date": summary["statement_date"],
        "payment_due_date": summary["payment_due_date"],
        "currency": summary["currency"],
        "opening_balance": _plain_amount(summary["previous_balance"]),
        "closing_balance": _plain_amount(summary["new_balance"]),
        "summary": summary,
        "summary_from_text": sorted(from_text),
        "section_totals": sections,
        "transactions": transactions,
        "removed_rows": removed,
        # Printed transaction lines that no answer matched (the model left them out).
        "missed_lines": missed,
        # Recorded for diagnosis only; the row check counts the PDF text instead.
        "rows_detected_in_text": sum(a.rows_detected_in_text or 0 for _, a in answers),
        "rows_returned": sum(a.rows_returned or 0 for _, a in answers),
        "pages_sent": [page for page, _ in answers],
    }


# ------------------------------------------------------------- classification, totals


class CardRow(BaseModel):
    """One transaction. ``amount`` is signed as printed (negative = credit)."""

    model_config = ConfigDict(frozen=True)

    id: str = ""
    line_no: int = 0
    date: dt.date | None = None
    posting_date: dt.date | None = None
    transaction_number: str = ""
    auth_number: str = ""
    description: str = ""
    card_last4: str = ""
    page: int | None = None
    pre_tax: Decimal | None = None
    tax: Decimal | None = None
    amount: Decimal | None = None


class Totals(BaseModel):
    total_pretax: Decimal | None
    total_tax: Decimal | None
    total_debits: Decimal
    total_credits: Decimal


class Balance(BaseModel):
    previous: Decimal | None
    total_credits: Decimal
    total_debits: Decimal
    calculated_closing: Decimal | None
    printed_closing: Decimal | None


class Check(BaseModel):
    """``ok`` is None when the check could not run. ``required``: then a person must look."""

    code: str
    label: str
    ok: bool | None
    message: str
    expected: Decimal | None = None
    actual: Decimal | None = None
    required: bool = False

    @property
    def failed(self) -> bool:
        return self.ok is False or (self.ok is None and self.required)


def classify(amount: Decimal | None) -> Literal["debit", "credit"] | None:
    """The printed sign decides, nothing else."""
    if amount is None:
        return None
    return "credit" if amount < 0 else "debit"


def split_rows(rows: Iterable[CardRow]) -> tuple[list[CardRow], list[CardRow], list[CardRow]]:
    """(debits, credits, rows without an amount)."""
    debits: list[CardRow] = []
    credits: list[CardRow] = []
    unknown: list[CardRow] = []
    for row in rows:
        kind = classify(row.amount)
        (debits if kind == "debit" else credits if kind == "credit" else unknown).append(row)
    return debits, credits, unknown


def _sum(values: Iterable[Decimal | None]) -> Decimal:
    return sum((v for v in values if v is not None), ZERO)


def compute_totals(rows: Iterable[CardRow]) -> Totals:
    debits, credits, _ = split_rows(rows)
    taxed = any(row.tax is not None or row.pre_tax is not None for row in debits)
    return Totals(
        total_pretax=_sum(row.pre_tax for row in debits) if taxed else None,
        total_tax=_sum(row.tax for row in debits) if taxed else None,
        total_debits=_sum(row.amount for row in debits),
        total_credits=-_sum(row.amount for row in credits),
    )


def calculated_closing(previous: Decimal, totals: Totals) -> Decimal:
    """Credits reduce the previous balance; they are never taken off the purchases."""
    return previous - totals.total_credits + totals.total_debits


Parts = list[tuple[str, Decimal]]


def credit_parts(summary: Mapping[str, Decimal | None]) -> Parts | None:
    """The printed numbers that make up the payments total, or None if none is printed.

    "Payments and credits" is already the whole total and is never added to. Only a bare
    "Payments" line is topped up with a negative cash advance or adjustment (BMO prints its
    cashback in the Cash Advance field).
    """
    total = summary.get("payments_and_credits")
    if total is not None:
        return [("Payments and credits", abs(total))]
    payments = summary.get("payments")
    if payments is None:
        return None
    parts = [("Payments", abs(payments))]
    for key, label in (("cash_advances", "Cash advance"), ("adjustments", "Adjustments")):
        value = summary.get(key)
        if value is not None and value < 0:
            parts.append((label, -value))
    return parts


def debit_parts(summary: Mapping[str, Decimal | None]) -> Parts | None:
    """Printed purchases plus the charges that are printed on their own line."""
    if (base := summary.get("purchases_and_other_charges")) is not None:
        parts = [("Purchases and other charges", base)]
    elif (base := summary.get("net_purchases")) is not None:
        parts = [("Net purchases", base)]
    else:
        return None
    for key, label in (
        ("cash_advances", "Cash advance"),
        ("interest", "Interest"),
        ("fees", "Fees"),
        ("other_charges", "Other charges"),
        ("adjustments", "Adjustments"),
    ):
        value = summary.get(key)
        if value is not None and value > 0:
            parts.append((label, value))
    return parts


def _total(parts: Parts | None) -> Decimal | None:
    return None if parts is None else sum((value for _, value in parts), ZERO)


def expected_credits(summary: Mapping[str, Decimal | None]) -> Decimal | None:
    return _total(credit_parts(summary))


def expected_debits(summary: Mapping[str, Decimal | None]) -> Decimal | None:
    return _total(debit_parts(summary))


def _same(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) < CENT


def _fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def run_checks(
    rows: Sequence[CardRow],
    *,
    previous: Decimal | None,
    printed_closing: Decimal | None,
    summary: Mapping[str, Decimal | None],
    sections: Sequence[Mapping[str, Any]],
    text_rows: int | None,
) -> list[Check]:
    """The five checks, always in the same order."""
    totals = compute_totals(rows)
    missing = [row.line_no for row in rows if row.amount is None]
    return [
        _closing_check(totals, previous, printed_closing, bool(missing)),
        _total_check("debits_ok", "Purchases", totals.total_debits, debit_parts(summary)),
        _total_check("credits_ok", "Payments", totals.total_credits, credit_parts(summary)),
        _rows_check(len(rows), text_rows, missing),
        _section_check(rows, sections),
    ]


def _closing_check(
    totals: Totals, previous: Decimal | None, printed: Decimal | None, missing: bool
) -> Check:
    label = "Closing balance"
    if previous is None or printed is None or missing:
        why = "a transaction has no amount" if missing else "a printed balance is missing"
        message = f"The closing balance cannot be checked because {why}."
        return Check(code="closing_ok", label=label, ok=None, message=message, required=True)
    actual = calculated_closing(previous, totals)
    ok = _same(actual, printed)
    message = f"Calculated closing {_fmt(actual)}, statement shows {_fmt(printed)}" + (
        "." if ok else f" (difference {_fmt(printed - actual)})."
    )
    return Check(
        code="closing_ok", label=label, ok=ok, message=message, expected=printed, actual=actual
    )


def _total_check(code: str, label: str, actual: Decimal, parts: Parts | None) -> Check:
    expected = _total(parts)
    if parts is None or expected is None:
        message = f"The statement prints no {label.lower()} total."
        return Check(code=code, label=f"{label} total", ok=None, message=message, actual=actual)
    ok = _same(actual, expected)
    # Say where the number comes from, so a sum is never mistaken for a printed figure.
    if len(parts) == 1:
        source = f"statement shows {_fmt(expected)}"
    else:
        joined = " + ".join(f"{name} {_fmt(value)}" for name, value in parts)
        source = f"statement: {joined} = {_fmt(expected)}"
    message = f"{label} add up to {_fmt(actual)}; {source}" + (
        "." if ok else f" (difference {_fmt(expected - actual)})."
    )
    return Check(
        code=code, label=f"{label} total", ok=ok, message=message, expected=expected, actual=actual
    )


def _rows_check(count: int, text_rows: int | None, missing: Sequence[int]) -> Check:
    label = "All rows extracted"
    if missing:
        numbers = ", ".join(str(n) for n in missing)
        message = f"No amount on line {numbers}."
        if text_rows is not None and text_rows != count:
            message += f" {count} rows extracted, {text_rows} rows printed in the PDF."
        return Check(code="rows_ok", label=label, ok=False, message=message)
    if text_rows is None:
        message = "The rows cannot be counted in this document (scan or unknown layout)."
        return Check(code="rows_ok", label=label, ok=None, message=message, required=True)
    message = f"{count} rows extracted, {text_rows} rows printed in the PDF."
    return Check(
        code="rows_ok",
        label=label,
        ok=count == text_rows,
        message=message,
        expected=Decimal(text_rows),
        actual=Decimal(count),
    )


def _section_check(rows: Sequence[CardRow], sections: Sequence[Mapping[str, Any]]) -> Check:
    label = "Totals per card"
    problems: list[str] = []
    compared = 0
    for section in sections:
        card = str(section.get("card_last4") or "")
        own = compute_totals(row for row in rows if row.card_last4 == card)
        for key, word in (("total_credits", "credits"), ("total_debits", "debits")):
            try:
                printed = parse_printed_amount(section.get(key))
            except ParseError:
                problems.append(f"card {card} {word} total is unreadable")
                continue
            if printed is None:
                continue
            compared += 1
            actual = own.total_credits if word == "credits" else own.total_debits
            if not _same(actual, abs(printed)):
                problems.append(f"card {card} {word} {_fmt(actual)}, printed {_fmt(abs(printed))}")
    if problems:
        message = f"Does not match: {'; '.join(problems)}."
        return Check(code="section_ok", label=label, ok=False, message=message)
    if not compared:
        message = "The statement prints no totals per card."
        return Check(code="section_ok", label=label, ok=None, message=message)
    message = f"All {compared} printed card totals match."
    return Check(code="section_ok", label=label, ok=True, message=message)
