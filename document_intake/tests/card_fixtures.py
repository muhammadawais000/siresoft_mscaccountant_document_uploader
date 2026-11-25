"""Two card statements with invented merchants and the real layouts and amounts.

The page texts look like pdfplumber output; ``*_PAGES`` are what a model that copies the
page correctly would answer.
"""

from __future__ import annotations

import io
import re
from typing import Any

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from document_intake.extraction import schemas
from document_intake.providers.base import ExtractionProvider, ModelRequest, ModelResponse

SUMMARY_KEYS = list(schemas.CARD_PAGE_SCHEMA["properties"]["summary"]["properties"])


def summary(**values: str) -> dict[str, Any]:
    return {**dict.fromkeys(SUMMARY_KEYS), **values}


def row(trans: str, posted: str, text: str, raw: str, **extra: Any) -> dict[str, Any]:
    return {
        "trans_date": trans,
        "posting_date": posted,
        "transaction_number": None,
        "auth_number": None,
        "description": text,
        "card_last4": None,
        "pre_tax_amount": None,
        "tax_amount": None,
        "amount": raw.replace("$", "").replace(",", "").replace(" ", ""),
        "amount_raw": raw,
        **extra,
    }


def page(rows: list[dict[str, Any]], **header: str) -> dict[str, Any]:
    return {
        "summary": summary(**header),
        "section_totals": [],
        "transactions": rows,
        "rows_detected_in_text": len(rows),
        "rows_returned": len(rows),
    }


# ---------------------------------------------------------------------------- BMO

_BMO_3664 = [
    "12/04 12/08 13.26 1.72 14.98",
    "12/05 12/08 630.51 81.97 712.48",
    "12/05 12/08 329.11 42.79 371.90",
    "12/05 12/08 330.31 42.94 373.25",
    "12/06 12/08 160.57 20.87 181.44",
    "12/10 12/11 527.02 68.51 595.53",
    "12/11 12/15 14.58 1.90 16.48",
    "12/12 12/15 517.68 67.30 584.98",
    "12/13 12/15 31.31 4.07 35.38",
    "12/14 12/15 17.26 2.24 19.50",
    "12/18 12/19 5.74 0.75 6.49",
    "12/18 12/22 28.30 3.68 31.98",
    "12/21 12/22 47.50 6.18 53.68",
    "12/24 12/26 25.61 3.33 28.94",
    "12/24 12/26 4.86 0.63 5.49",
    "12/25 12/29 1,025.90 133.37 1,159.27",
    "12/26 12/29 1,251.22 162.66 1,413.88",
    "12/29 12/30 9.03 1.17 10.20",
    "12/29 12/30 32.49 4.22 36.71",
    "12/29 12/30 12.37 1.61 13.98",
    "12/29 12/30 41.57 5.40 46.97",
    "12/29 12/31 7.06 0.92 7.98",
    "12/31 01/01 491.84 63.94 555.78",
    "01/02 01/02 330.31 42.94 373.25",
]


def _bmo_charge(number: int, source: str, note: str = " (e)") -> tuple[list[str], dict[str, Any]]:
    trans, posted, pre, tax, total = source.split()
    name, trans_id = f"SHOP {number:02d} TORONTO ON", f"6263{number:05d}"
    text = [
        f"{trans} {posted} {name} $ {pre} $ {tax}{note} $ {total}",
        f"{trans_id} {number:06d}",
    ]
    item = row(
        trans,
        posted,
        name,
        f"$ {total}",
        transaction_number=trans_id,
        auth_number=f"{number:06d}",
        pre_tax_amount=f"$ {pre}",
        tax_amount=f"{tax}{note}",
    )
    return text, item


_bmo = [_bmo_charge(i, s) for i, s in enumerate(_BMO_3664, 1)]
_bmo_5124 = _bmo_charge(25, "12/28 12/29 299.02 38.87 337.89", note="")
_BMO_CREDITS = [
    row("12/03", "12/04", "CASHBACK ANN PAY/REMB.", "$ -613.03", transaction_number="625696711"),
    row("12/24", "12/24", "AUTOMATIC PYMT RECEIVED", "$ -6,683.23", transaction_number="629036302"),
]


def _lines(items: list[tuple[list[str], dict[str, Any]]]) -> list[str]:
    return [line for text, _ in items for line in text]


BMO_TEXT = [
    [
        "Statement",
        "Account Name: EXAMPLE INC Card Number: xxxx-xxxx-xxxx-6915",
        "Statement Date (MM/DD/YYYY): 01/03/2026 Currency: CANADIAN DOLLAR",
        "Previous Balance: $ 7,296.26",
        "Payments: $ -6,683.23",
        "Net Purchases: $ 6,988.41",
        "Cash Advance: $ -613.03",
        "New Account Balance: $ 6,988.41",
        "Card Number xxxx-xxxx-xxxx-6915 1234567 EXAMPLE INC",
        "12/03 12/04 CASHBACK ANN PAY/REMB. $ -613.03 $ 0.00 $ -613.03",
        "625696711",
        "12/24 12/24 AUTOMATIC PYMT RECEIVED $ -6,683.23 $ 0.00 $ -6,683.23",
        "629036302",
        "TOTAL CREDITS xxxx-xxxx-xxxx-6915 $ -7,296.26",
        "TOTAL DEBITS xxxx-xxxx-xxxx-6915 $ 0.00",
        "Card Number xxxx-xxxx-xxxx-3664 HOLDER ONE",
        *_lines(_bmo[:3]),
    ],
    [
        *_lines(_bmo[3:]),
        "TOTAL CREDITS xxxx-xxxx-xxxx-3664 $ 0.00",
        "TOTAL DEBITS xxxx-xxxx-xxxx-3664 $ 6,650.52",
        "Card Number xxxx-xxxx-xxxx-5124 HOLDER TWO",
        *_lines([_bmo_5124]),
    ],
    [
        "Page 3 of 5",
        "TOTAL CREDITS xxxx-xxxx-xxxx-5124 $ 0.00",
        "TOTAL DEBITS xxxx-xxxx-xxxx-5124 $ 337.89",
    ],
    ["CUSTOMER SERVICE:", "Telephone Inquiries: 1-800-555-0100", "Lost/Stolen cards: call us"],
    ["PAYMENT INFORMATION:", "You can mail your payment to: PO Box 1"],
]
BMO_PAGES = {
    1: page(
        [*_BMO_CREDITS, *(item for _, item in _bmo[:3])],
        institution="BMO",
        card_last4="6915",
        currency="CAD",
        statement_date="01/03/2026",
        previous_balance="$ 7,296.26",
        new_balance="$ 6,988.41",
        payments="$ -6,683.23",
        net_purchases="$ 6,988.41",
        cash_advances="$ -613.03",
        interest=None,
        fees="$ 0.00",
        adjustments="$ 0.00",
        other_charges="$ 0.00",
    ),
    2: page([*(item for _, item in _bmo[3:]), _bmo_5124[1]]),
}

# ----------------------------------------------------------------------------- TD

_TD_1 = """OCT6 OCT7 1,454.92|OCT6 OCT7 321.99|OCT5 OCT8 91.27|OCT7 OCT8 996.25|OCT7 OCT8 590.30
OCT7 OCT8 -2,000.00|OCT7 OCT9 9.70|OCT7 OCT9 1,135.87|OCT9 OCT10 594.61|OCT9 OCT10 2,923.41
OCT9 OCT10 -4,000.00|OCT10 OCT14 46.99|OCT14 OCT15 3,028.67|OCT14 OCT15 -2,000.00
OCT15 OCT16 186.41"""
_TD_3 = """OCT15 OCT16 27.00|OCT15 OCT17 29.45|OCT15 OCT17 560.98|OCT16 OCT17 560.48
OCT17 OCT20 787.28
OCT17 OCT20 -4,000.00|OCT20 OCT21 157.21|OCT20 OCT21 -3,000.00|OCT20 OCT22 67.78
OCT21 OCT22 1,943.25|OCT21 OCT22 586.92|OCT21 OCT22 -5,731.00|OCT22 OCT23 547.18
OCT22 OCT23 107.35|OCT23 OCT24 681.58|OCT26 OCT27 30.14|OCT27 OCT28 897.55|OCT27 OCT28 156.73
OCT27 OCT28 1,147.23|OCT29 OCT30 46.33|OCT29 OCT31 570.81|OCT30 OCT31 -1,771.90
OCT31 NOV3 553.08|NOV2 NOV4 32.76|NOV3 NOV4 617.95"""


def _td(source: str, side: dict[int, str] | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    text, items = [], []
    for i, part in enumerate(re.split(r"[|\n]", source)):
        trans, posted, amount = part.split()
        raw = f"-${amount[1:]}" if amount.startswith("-") else f"${amount}"
        name = "PAYMENT-THANKYOU" if amount.startswith("-") else f"STORE{i}905-555-0100"
        note = (side or {}).get(i, "")
        text.append(f"{trans} {posted} {name} {raw} {note}".rstrip())
        items.append(row(trans, posted, name, raw))
    return text, items


_td1 = _td(_TD_1, {5: "", 11: "PreviousBalance $20,730.89", 13: "CashAdvances $0.00"})
_td3 = _td(_TD_3)
TD_TEXT = [
    [
        "STATEMENTDATE:November05,2025 1OF3",
        "STATEMENTPERIOD:October07,2025toNovember05,2025",
        "PREVIOUSSTATEMENTBALANCE $20,730.89 Earnedthisstatementperiod + 42,981",
        *_td1[0],
        "Payments&Credits $22,502.90",
        "Purchases&OtherCharges $21,489.43",
        "NEWBALANCE $19,717.42",
    ],
    ["Interest is calculated daily. See your cardholder agreement."],
    ["PREVIOUSSTATEMENT:October06,2025", *_td3[0], "TOTALNEWBALANCE $19,717.42"],
]
TD_PAGES = {
    1: page(
        # A model that wrongly returns the two summary lines as rows.
        [row("OCT6", None, "PREVIOUSSTATEMENTBALANCE", "$20,730.89"), *_td1[1]],
        institution="TD Canada Trust",
        card_last4="8054",
        currency="CAD",
        statement_date="November05,2025",
        period_start="2025-10-07",
        period_end="2025-11-05",
        previous_balance="$20,730.89",
        new_balance="$19,717.42",
        payments_and_credits="$22,502.90",
        purchases_and_other_charges="$21,489.43",
        cash_advances="$0.00",
        interest="$0.00",
        fees="$0.00",
    ),
    3: page([*_td3[1], row(None, None, "TOTAL NEW BALANCE", "$19,717.42")]),
}


def make_pdf(pages: list[list[str]]) -> bytes:
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=letter)
    for lines in pages:
        pdf.setFont("Helvetica", 8)
        for i, line in enumerate(lines):
            pdf.drawString(40, 760 - 12 * i, line)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


class PageProvider(ExtractionProvider):
    """Answers each "This is page N" request with that page's data."""

    name = "fake"

    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.pages = pages
        self.asked: list[int] = []
        self.requests: list[ModelRequest] = []

    def model_for(self, *, vision: bool) -> str:
        return "fake-vision" if vision else "fake-text"

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        match = re.search(r"This is page (\d+) of", request.user)
        assert match
        number = int(match[1])
        self.asked.append(number)
        self.requests.append(request)
        return ModelResponse(self.pages[number], "fake-text", 1)
