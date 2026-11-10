"""Test doubles for the extraction provider."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from document_intake.extraction import schemas
from document_intake.providers.base import ExtractionProvider, ModelRequest, ModelResponse

Responder = Callable[[ModelRequest], dict[str, Any]]


class FakeProvider(ExtractionProvider):
    """Answers classification with ``doc_type`` and extraction with ``data``."""

    name = "fake"

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        doc_type: str = "receipt",
        error: Exception | None = None,
    ) -> None:
        self.data = data or {}
        self.doc_type = doc_type
        self.error = error
        self.requests: list[ModelRequest] = []

    def model_for(self, *, vision: bool) -> str:
        return "fake-vision" if vision else "fake-text"

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.error:
            raise self.error
        if request.schema is schemas.CLASSIFICATION_SCHEMA:
            data: dict[str, Any] = {"doc_type": self.doc_type}
        else:
            data = self.data
        return ModelResponse(
            data=data, model_name=self.model_for(vision=request.vision), duration_ms=5
        )


BANK_STATEMENT = {
    "institution_name": "Sample Bank",
    "account_last4": "1234",
    "statement_period_start": "2026-09-01",
    "statement_period_end": "2026-09-30",
    "currency": "CAD",
    "opening_balance": "5000.00",
    "closing_balance": "4200.00",
    "transactions": [
        {
            "date": "2026-09-03",
            "description": "Rent",
            "debit": "1500.00",
            "credit": None,
            "balance": "3500.00",
        },
        {
            "date": "2026-09-15",
            "description": "Client payment",
            "debit": None,
            "credit": "700.00",
            "balance": "4200.00",
        },
    ],
}

RECEIPT = {
    "vendor_name": "Hardware Store",
    "invoice_number": None,
    "invoice_date": "2026-09-03",
    "due_date": None,
    "currency": None,
    "subtotal": "100.00",
    "gst": None,
    "hst": "13.00",
    "pst": None,
    "total": "113.00",
    "payment_method": "VISA",
    "customer_job": None,
    "description": None,
    "line_items": [
        {
            "description": "Screws",
            "quantity": "2",
            "unit_price": "25.00",
            "amount": "50.00",
            "tax": None,
        },
        {
            "description": "Drill bit",
            "quantity": "1",
            "unit_price": "50.00",
            "amount": "50.00",
            "tax": None,
        },
    ],
}
