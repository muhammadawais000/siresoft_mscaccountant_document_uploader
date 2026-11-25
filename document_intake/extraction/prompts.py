"""Versioned prompts. Bump PROMPT_VERSION on any wording change; it is stored per result."""

from __future__ import annotations

from document_intake.models import DocType

PROMPT_VERSION = "2026-11-26.1"

SYSTEM_PROMPT = """You extract data from Canadian accounting documents for a bookkeeper.
Rules:
- Return only JSON that matches the provided schema.
- Copy values exactly as printed. Never guess, infer, calculate or fill in a value.
- If a value is missing, cut off, blurry or not clearly legible, return null for it.
- Amounts: plain decimal strings with a dot and no currency symbol or thousands separator,
  for example "1234.56". Use a leading minus only when the document shows a negative amount.
- Dates: ISO-8601 YYYY-MM-DD. If the year is not printed, return null.
- Currency: an ISO code. Use {default_currency} only when the document is clearly
  Canadian and shows no other currency.
- Never output full account or card numbers; only the last 4 digits."""

# Card statements are copied as printed; code does the rest (``statement_processor``).
CARD_SYSTEM_PROMPT = """You copy data from Canadian credit card statements for a bookkeeper.
Rules:
- Return only JSON that matches the provided schema.
- Copy values exactly as printed. Never guess, infer, calculate or fill in a value.
- If a value is missing, cut off, blurry or not clearly legible, return null for it.
- Currency: an ISO code. Use {default_currency} only when the document is clearly
  Canadian and shows no other currency.
- Never output full account or card numbers; only the last 4 digits."""

# Statements often list several accounts or cardholders, each with its own subtotals.
# Small models tend to stop at the first "TOTAL" line, so say plainly that it is not the end.
STATEMENT_RULES = (
    " The statement may have several sections, one per account, card or cardholder, each "
    "ending with its own TOTAL, subtotal or summary lines. Those lines are not the end of the "
    "statement and are not transactions: keep going and extract the transactions of every "
    "section on every page, in the order printed."
)

TASK_PROMPTS: dict[str, str] = {
    DocType.RECEIPT: (
        "This is a receipt. Extract the vendor, date, subtotal, each tax shown "
        "(GST, HST, PST separately, as printed), total, payment method and the line items."
    ),
    DocType.INVOICE: (
        "This is a supplier invoice or bill. Extract the vendor, invoice number, invoice date, "
        "due date, subtotal, each tax shown (GST, HST, PST separately), total and line items."
    ),
    DocType.BANK_STATEMENT: (
        "This is a bank statement. Extract the institution, last 4 digits of the account, the "
        "statement period, opening and closing balance, and every transaction in order. "
        "Money leaving the account is a debit; money coming in is a credit." + STATEMENT_RULES
    ),
    DocType.CREDIT_CARD_STATEMENT: (
        "This is one page of a credit card statement. Copy what is printed on this page.\n"
        "- transactions: every dated row, in printed order, including payments, cashback and "
        "refunds. Do not decide debit or credit and do not add anything up.\n"
        "- amount_raw: the row's final amount (including tax), exactly as printed with its "
        'sign, for example "-$2,000.00" or "$ -613.03". amount: the same value as a plain '
        'decimal with the same sign, for example "-2000.00".\n'
        "- If other text follows the amount on the same line (a side box such as CashAdvances "
        "$0.00), it is not part of the row: use the first amount after the description.\n"
        '- trans_date and posting_date: exactly as printed ("12/03", "OCT6"). Do not add a '
        "year.\n"
        "- transaction_number: the Trans ID or reference printed with the row. auth_number: the "
        "Auth # printed with the row. They may be on the line below; they are not new rows.\n"
        '- If there is a tax column, tax_amount is the tax without its note ("1.72 (e)" is '
        '"1.72") and pre_tax_amount is the amount before tax. Otherwise both are null.\n'
        "- A line with only a phone number or city belongs to the row above it.\n"
        "- Not transactions: PREVIOUS STATEMENT BALANCE, TOTAL NEW BALANCE, NEW BALANCE, TOTAL "
        "CREDITS, TOTAL DEBITS, Sub-total, rewards lines, headers, legal text and ads.\n"
        "- section_totals: the TOTAL CREDITS and TOTAL DEBITS lines printed per card.\n"
        "- summary: the statement summary if it is on this page, every amount with its printed "
        "sign. statement_date, period_start and period_end as YYYY-MM-DD.\n"
        "- The page may start in the middle of a card's section. A TOTAL line is not the end of "
        "the page: keep going.\n"
        "- rows_detected_in_text: how many transaction rows you see; rows_returned: how many you "
        "returned."
    ),
}

CLASSIFY_PROMPT = (
    "Classify this accounting document as exactly one of: bank_statement, "
    "credit_card_statement, receipt, invoice."
)


def system_prompt(default_currency: str) -> str:
    return SYSTEM_PROMPT.format(default_currency=default_currency)


def text_user_prompt(task: str, document_text: str) -> str:
    return f"{task}\n\nDocument text (extracted from the PDF):\n<<<\n{document_text}\n>>>"


def image_user_prompt(task: str, page_count: int) -> str:
    pages = "the attached page image" if page_count == 1 else f"the {page_count} page images"
    return f"{task}\n\nThe document is {pages}, in page order."


def card_system_prompt(default_currency: str) -> str:
    return CARD_SYSTEM_PROMPT.format(default_currency=default_currency)


def card_page_prompt(task: str, number: int, count: int, card_last4: str) -> str:
    """The task for one page, with the card whose section is still open from the page before."""
    text = f"{task}\n\nThis is page {number} of {count}."
    if card_last4:
        text += f" It starts inside the section of card {card_last4}."
    return text
