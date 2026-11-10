"""Versioned prompts. Bump PROMPT_VERSION on any wording change; it is stored per result."""

from __future__ import annotations

from document_intake.models import DocType

PROMPT_VERSION = "2026-11-10.1"

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
        "Money leaving the account is a debit; money coming in is a credit."
    ),
    DocType.CREDIT_CARD_STATEMENT: (
        "This is a credit card statement. Extract the issuer, last 4 digits of the card, the "
        "statement period, previous balance as opening_balance, new balance as "
        "closing_balance, and every transaction in order. Purchases, fees and interest are "
        "debits; payments and refunds are credits."
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
