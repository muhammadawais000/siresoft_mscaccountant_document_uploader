"""CSV export of approved data, in QuickBooks-Desktop-friendly columns.

Statements: one row per transaction  -> Date, Description, Debit, Credit, Balance
Receipts / invoices: one row per doc -> Date, Vendor, Account, Description, Subtotal, Tax,
                                        Total, Currency
Only approved (or already exported) documents can be exported.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any

from django.db import transaction

from document_intake.conf import intake_settings
from document_intake.exceptions import InvalidTransition
from document_intake.extraction.schemas import family
from document_intake.models import AuditAction, Document, DocumentStatus, ExtractionResult

from . import audit, lifecycle

EXPORTABLE = (DocumentStatus.APPROVED, DocumentStatus.EXPORTED)
STATEMENT_COLUMNS = ["Date", "Description", "Debit", "Credit", "Balance"]
RECEIPT_COLUMNS = [
    "Date",
    "Vendor",
    "Account",
    "Description",
    "Subtotal",
    "Tax",
    "Total",
    "Currency",
]
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value: str) -> str:
    """Neutralise spreadsheet formula injection in free-text cells."""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _statement_rows(result: ExtractionResult) -> list[list[str]]:
    return [
        [
            line.date.isoformat() if line.date else "",
            safe_text(line.description),
            _money(line.debit),
            _money(line.credit),
            _money(line.balance),
        ]
        for line in result.lines.order_by("line_no")
    ]


def _receipt_rows(result: ExtractionResult, currency_default: str) -> list[list[str]]:
    rows = list(result.fields.all())
    text = {f.key: f.value for f in rows}
    # Amounts and dates come from the normalised column only; an unreadable value that a
    # reviewer acknowledged exports as blank rather than as garbage.
    norm = {f.key: f.normalized_value for f in rows}
    taxes = [Decimal(norm[k]) for k in ("gst", "hst", "pst") if norm.get(k)]
    return [
        [
            norm.get("invoice_date", ""),
            safe_text(text.get("vendor_name", "")),
            safe_text(text.get("account", "")),
            safe_text(text.get("description", "")),
            norm.get("subtotal", ""),
            _money(sum(taxes, Decimal("0.00"))) if taxes else "",
            norm.get("total", ""),
            norm.get("currency", "") or currency_default,
        ]
    ]


def export_csv(document: Document, actor: Any) -> tuple[str, str]:
    """Return (filename, csv text). The first export moves the document to ``exported``."""
    if document.status not in EXPORTABLE:
        raise InvalidTransition("Only approved documents can be exported.")
    result = document.latest_result()
    if result is None:  # pragma: no cover - approved always has a result
        raise InvalidTransition("There is no extraction result to export.")

    statement = family(document.effective_doc_type) == "statement"
    columns = STATEMENT_COLUMNS if statement else RECEIPT_COLUMNS
    rows = (
        _statement_rows(result)
        if statement
        else _receipt_rows(result, str(intake_settings.DEFAULT_CURRENCY))
    )
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(columns)
    writer.writerows(rows)

    with transaction.atomic():
        if document.status == DocumentStatus.APPROVED:
            lifecycle.mark_exported(document, actor)
        audit.record(
            AuditAction.EXPORT,
            document,
            actor,
            format="csv",
            rows=len(rows),
            version=result.version,
        )
    stem = document.original_filename.rsplit(".", 1)[0] or "document"
    return f"{stem}-v{result.version}.csv", out.getvalue()
