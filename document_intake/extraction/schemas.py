"""JSON Schemas sent to the model (Ollama ``format``) and used to validate its output.

Conventions the prompt also states: every value may be null when not clearly legible;
amounts are decimal strings; dates are ISO-8601 (YYYY-MM-DD). We still re-parse every
value defensively because small models do not always honour the schema.
"""

from __future__ import annotations

from typing import Any

from document_intake.models import DocType

_NULLABLE_STR: dict[str, Any] = {"type": ["string", "null"]}
_AMOUNT: dict[str, Any] = {
    "type": ["string", "null"],
    "description": "Decimal string such as 1234.56 or -12.00; null if not clearly legible",
}
_DATE: dict[str, Any] = {
    "type": ["string", "null"],
    "description": "ISO date YYYY-MM-DD; null if not clearly legible",
}
_CURRENCY: dict[str, Any] = {
    "type": ["string", "null"],
    "description": "ISO currency code such as CAD or USD",
}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


RECEIPT_INVOICE_SCHEMA = _obj(
    {
        "vendor_name": _NULLABLE_STR,
        "invoice_number": _NULLABLE_STR,
        "invoice_date": _DATE,
        "due_date": _DATE,
        "currency": _CURRENCY,
        "subtotal": _AMOUNT,
        "gst": _AMOUNT,
        "hst": _AMOUNT,
        "pst": _AMOUNT,
        "total": _AMOUNT,
        "payment_method": _NULLABLE_STR,
        "customer_job": _NULLABLE_STR,
        "description": _NULLABLE_STR,
        "line_items": {
            "type": "array",
            "items": _obj(
                {
                    "description": _NULLABLE_STR,
                    "quantity": _AMOUNT,
                    "unit_price": _AMOUNT,
                    "amount": _AMOUNT,
                    "tax": _AMOUNT,
                }
            ),
        },
    }
)

STATEMENT_SCHEMA = _obj(
    {
        "institution_name": _NULLABLE_STR,
        "account_last4": {
            "type": ["string", "null"],
            "description": "Only the last 4 digits of the account or card number",
        },
        "statement_period_start": _DATE,
        "statement_period_end": _DATE,
        "currency": _CURRENCY,
        "opening_balance": _AMOUNT,
        "closing_balance": _AMOUNT,
        "transactions": {
            "type": "array",
            "items": _obj(
                {
                    "date": _DATE,
                    "description": _NULLABLE_STR,
                    "debit": _AMOUNT,
                    "credit": _AMOUNT,
                    "balance": _AMOUNT,
                }
            ),
        },
    }
)

_PRINTED: dict[str, Any] = {
    "type": ["string", "null"],
    "description": "Exactly as printed, with its sign; null if not printed",
}
_COUNT: dict[str, Any] = {"type": ["integer", "null"]}

# One page of a credit card statement, copied as printed. Debit/credit, totals and checks
# are worked out in ``statement_processor``, never by the model.
CARD_PAGE_SCHEMA = _obj(
    {
        "summary": _obj(
            {
                "institution": _NULLABLE_STR,
                "card_last4": _NULLABLE_STR,
                "currency": _CURRENCY,
                "statement_date": _DATE,
                "period_start": _DATE,
                "period_end": _DATE,
                "payment_due_date": _DATE,
                "previous_balance": _PRINTED,
                "new_balance": _PRINTED,
                "payments": _PRINTED,
                "payments_and_credits": _PRINTED,
                "purchases_and_other_charges": _PRINTED,
                "net_purchases": _PRINTED,
                "cash_advances": _PRINTED,
                "interest": _PRINTED,
                "fees": _PRINTED,
                "adjustments": _PRINTED,
                "other_charges": _PRINTED,
            }
        ),
        "section_totals": {
            "type": "array",
            "items": _obj(
                {"card_last4": _NULLABLE_STR, "total_credits": _PRINTED, "total_debits": _PRINTED}
            ),
        },
        "transactions": {
            "type": "array",
            "items": _obj(
                {
                    "trans_date": _PRINTED,
                    "posting_date": _PRINTED,
                    "transaction_number": _NULLABLE_STR,
                    "auth_number": _NULLABLE_STR,
                    "description": _NULLABLE_STR,
                    "card_last4": _NULLABLE_STR,
                    "pre_tax_amount": _PRINTED,
                    "tax_amount": _PRINTED,
                    "amount": _AMOUNT,
                    "amount_raw": _PRINTED,
                }
            ),
        },
        "rows_detected_in_text": _COUNT,
        "rows_returned": _COUNT,
    }
)

CLASSIFICATION_SCHEMA = _obj(
    {
        "doc_type": {
            "type": "string",
            "enum": [
                DocType.BANK_STATEMENT.value,
                DocType.CREDIT_CARD_STATEMENT.value,
                DocType.RECEIPT.value,
                DocType.INVOICE.value,
            ],
        }
    }
)

SCHEMA_FOR_TYPE: dict[str, dict[str, Any]] = {
    DocType.RECEIPT: RECEIPT_INVOICE_SCHEMA,
    DocType.INVOICE: RECEIPT_INVOICE_SCHEMA,
    DocType.BANK_STATEMENT: STATEMENT_SCHEMA,
    DocType.CREDIT_CARD_STATEMENT: CARD_PAGE_SCHEMA,
}

# Header fields shown in the review form, in display order: (key, label).
HEADER_FIELDS: dict[str, list[tuple[str, str]]] = {
    "receipt_invoice": [
        ("vendor_name", "Vendor"),
        ("invoice_number", "Invoice #"),
        ("invoice_date", "Invoice date"),
        ("due_date", "Due date"),
        ("currency", "Currency"),
        ("subtotal", "Subtotal"),
        ("gst", "GST"),
        ("hst", "HST"),
        ("pst", "PST"),
        ("total", "Total"),
        ("payment_method", "Payment method"),
        ("customer_job", "Customer / job"),
        ("description", "Description"),
        # Entered by the reviewer; never asked of the model (not printed on documents).
        ("posting_date", "Posting date"),
        ("account", "Account (head of account)"),
    ],
    "statement": [
        ("institution_name", "Institution"),
        ("account_last4", "Account (last 4)"),
        ("statement_period_start", "Period start"),
        ("statement_period_end", "Period end"),
        ("currency", "Currency"),
        ("opening_balance", "Opening balance"),
        ("closing_balance", "Closing balance"),
    ],
}

# Credit card statements show two more dates, after the period.
CARD_HEADER_FIELDS: list[tuple[str, str]] = [
    *HEADER_FIELDS["statement"][:4],
    ("statement_date", "Statement date"),
    ("payment_due_date", "Payment due date"),
    *HEADER_FIELDS["statement"][4:],
]


def header_fields(doc_type: str) -> list[tuple[str, str]]:
    if doc_type == DocType.CREDIT_CARD_STATEMENT:
        return CARD_HEADER_FIELDS
    return HEADER_FIELDS[family(doc_type)]


# Field kinds drive normalisation (see parsing.py).
AMOUNT_KEYS = frozenset(
    {"subtotal", "gst", "hst", "pst", "total", "opening_balance", "closing_balance"}
)
DATE_KEYS = frozenset(
    {
        "statement_date",
        "payment_due_date",
        "invoice_date",
        "due_date",
        "posting_date",
        "statement_period_start",
        "statement_period_end",
    }
)
# Fields the reviewer fills in; blank is their normal starting state, not "missing".
REVIEWER_KEYS = frozenset({"posting_date", "account"})


def family(doc_type: str) -> str:
    """``statement`` or ``receipt_invoice``."""
    if doc_type in (DocType.BANK_STATEMENT, DocType.CREDIT_CARD_STATEMENT):
        return "statement"
    return "receipt_invoice"
