"""Demo data for ``manage.py seed_demo``: sample PDFs and canned extraction output.

The PDFs live in ``fixtures/demo/`` (text-layer PDFs; the "pdf" lines below are their
printed text). A canned provider replaces the model so demos work without Ollama.
Nothing here is used by the real pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from document_intake.extraction import schemas
from document_intake.providers.base import ExtractionProvider, ModelRequest, ModelResponse

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "demo"


BANK_STATEMENT = {
    "institution_name": "Northern Trust Bank (demo)",
    "account_last4": "4821",
    "statement_period_start": "2026-09-01",
    "statement_period_end": "2026-09-30",
    "currency": "CAD",
    "opening_balance": "12450.00",
    "closing_balance": "10918.65",
    "transactions": [
        {
            "date": "2026-09-02",
            "description": "Rent - September",
            "debit": "2800.00",
            "credit": None,
            "balance": "9650.00",
        },
        {
            "date": "2026-09-08",
            "description": "Client deposit - Invoice 1043",
            "debit": None,
            "credit": "3390.00",
            "balance": "13040.00",
        },
        {
            "date": "2026-09-15",
            "description": "Hydro One",
            "debit": "184.35",
            "credit": None,
            "balance": "12855.65",
        },
        {
            "date": "2026-09-22",
            "description": "Payroll",
            "debit": "1937.00",
            "credit": None,
            "balance": "10918.65",
        },
    ],
}

# Deliberately inconsistent: the closing balance and one running balance do not add up,
# so the demo shows validation errors.
CARD_STATEMENT = {
    "institution_name": "Maple Card (demo)",
    "account_last4": "7730",
    "statement_period_start": "2026-09-01",
    "statement_period_end": "2026-09-30",
    "currency": "CAD",
    "opening_balance": "1250.40",
    "closing_balance": "1689.97",
    "transactions": [
        {
            "date": "2026-09-04",
            "description": "Office supplies",
            "debit": "212.43",
            "credit": None,
            "balance": "1462.83",
        },
        {
            "date": "2026-09-10",
            "description": "Payment - thank you",
            "debit": None,
            "credit": "1250.40",
            "balance": "212.43",
        },
        {
            "date": "2026-09-18",
            "description": "Software subscription",
            "debit": "56.50",
            "credit": None,
            "balance": "286.93",
        },
        {
            "date": "2026-10-03",
            "description": "Fuel",
            "debit": "84.12",
            "credit": None,
            "balance": "353.05",
        },
    ],
}

RECEIPT = {
    "vendor_name": "Hardware Depot (demo)",
    "invoice_number": "R-55821",
    "invoice_date": "2026-09-12",
    "due_date": None,
    "currency": None,
    "subtotal": "145.00",
    "gst": None,
    "hst": "18.85",
    "pst": None,
    "total": "163.85",
    "payment_method": "VISA ****7730",
    "customer_job": None,
    "description": "Shelving for storage room",
    "line_items": [
        {
            "description": "Steel shelf unit",
            "quantity": "1",
            "unit_price": "119.00",
            "amount": "119.00",
            "tax": None,
        },
        {
            "description": "Anchor kit",
            "quantity": "2",
            "unit_price": "13.00",
            "amount": "26.00",
            "tax": None,
        },
    ],
}

DOCUMENTS: list[dict[str, Any]] = [
    {
        "filename": "demo-bank-statement-sept-2026.pdf",
        "doc_type": "bank_statement",
        "data": BANK_STATEMENT,
        "pdf": [
            "Northern Trust Bank - Business Chequing (demo)",
            "Account ****4821",
            "Statement period 2026-09-01 to 2026-09-30",
            "Opening balance 12,450.00",
            "2026-09-02 Rent - September 2,800.00 9,650.00",
            "2026-09-08 Client deposit - Invoice 1043 3,390.00 13,040.00",
            "2026-09-15 Hydro One 184.35 12,855.65",
            "2026-09-22 Payroll 1,937.00 10,918.65",
            "Closing balance 10,918.65",
        ],
    },
    {
        "filename": "demo-card-statement-sept-2026.pdf",
        "doc_type": "credit_card_statement",
        "data": CARD_STATEMENT,
        "pdf": [
            "Maple Card - Business Visa (demo)",
            "Card ****7730",
            "Statement period 2026-09-01 to 2026-09-30",
            "Previous balance 1,250.40",
            "2026-09-04 Office supplies 212.43",
            "2026-09-10 Payment - thank you -1,250.40",
            "2026-09-18 Software subscription 56.50",
            "2026-10-03 Fuel 84.12",
            "New balance 1,689.97",
        ],
    },
    {
        "filename": "demo-receipt-hardware.pdf",
        "doc_type": "receipt",
        "data": RECEIPT,
        "pdf": [
            "Hardware Depot (demo)",
            "Receipt R-55821   2026-09-12",
            "Steel shelf unit 1 x 119.00 119.00",
            "Anchor kit 2 x 13.00 26.00",
            "Subtotal 145.00",
            "HST 13% 18.85",
            "Total 163.85",
            "Paid VISA ****7730",
        ],
    },
]


class CannedProvider(ExtractionProvider):
    """Returns fixed data instead of calling a model."""

    name = "demo"

    def __init__(self, data: dict[str, Any], doc_type: str) -> None:
        self.data = data
        self.doc_type = doc_type

    def model_for(self, *, vision: bool) -> str:
        return "demo-canned"

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        if request.schema is schemas.CLASSIFICATION_SCHEMA:
            return ModelResponse({"doc_type": self.doc_type}, "demo-canned", 1)
        return ModelResponse(self.data, "demo-canned", 1)


def pdf_for(spec: dict[str, Any]) -> bytes:
    return (FIXTURES / str(spec["filename"])).read_bytes()
