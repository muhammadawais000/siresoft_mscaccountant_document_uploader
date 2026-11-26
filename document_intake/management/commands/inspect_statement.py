"""Show what the code reads from a statement PDF, without calling the model.

Usage: ``python manage.py inspect_statement path/to/statement.pdf``

For a new bank: run this first. Every line marked "not found" is something the code cannot
check on its own; most are fixed by adding the bank's label wording to
``DOCUMENT_INTAKE["CARD_SUMMARY_LABELS"]``. Nothing is stored and nothing is sent anywhere.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from document_intake.exceptions import IntakeError
from document_intake.extraction import parsing, sources
from document_intake.extraction import statement_processor as processor

AMOUNT_KEYS = (
    "previous_balance",
    "new_balance",
    "payments",
    "payments_and_credits",
    "purchases_and_other_charges",
    "net_purchases",
    "cash_advances",
    "interest",
    "fees",
    "adjustments",
    "other_charges",
)
DATE_KEYS = ("statement_date", "payment_due_date", "period_start", "period_end")


class Command(BaseCommand):
    help = "Show what the code reads from a statement PDF (no model call, nothing stored)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("pdf", help="Path to the statement PDF")

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["pdf"])
        if not path.is_file():
            raise CommandError(f"No such file: {path}")
        try:
            pdf = sources.read_pdf_text(io.BytesIO(path.read_bytes()))
        except IntakeError as exc:
            raise CommandError(str(exc)) from exc
        out = self.stdout.write
        chars = [sources.readable_chars(page) for page in pdf.pages]
        out(f"Pages: {pdf.page_count}; readable characters per page: {chars}")
        if not pdf.has_text_layer():
            out("Route: VISION (scan or no usable text). Text checks do not apply.")
            return
        out("Route: PDF text")
        pages = processor.split_pages(pdf.text)
        layout = processor.read_layout(pages)

        rows = processor.count_rows(pages)
        out("")
        out(
            f"Transaction lines found: {rows if rows is not None else 'not found (layout unknown)'}"
        )
        for row in layout.rows[:3]:
            out(f"  e.g. page {row.page}: {row.line[:90]}")
        sent = [n for n, page in enumerate(pages, 1) if processor.needs_model(page, n)]
        out(f"Pages sent to the model: {sent}")
        payments = sum(1 for row in layout.rows if row.payment_section)
        if payments:
            out(f"Rows under a payments heading (taken as credits): {payments}")

        out("")
        out(f"Card sections: {'yes' if layout.has_sections else 'none printed'}")
        for card, totals in layout.footers.items():
            out(f"  card {card}: credits {totals.get('credits')}, debits {totals.get('debits')}")

        summary = processor.read_summary(pages)
        out("")
        out("Summary read next to its label:")
        for key in (*DATE_KEYS, *AMOUNT_KEYS):
            value = summary.get(key)
            if value is None:
                out(f"  {key:<28} not found")
                continue
            if key in AMOUNT_KEYS:
                try:
                    value = f"{value}  ->  {parsing.parse_printed_amount(value)}"
                except parsing.ParseError:
                    value = f"{value}  ->  UNREADABLE"
            out(f"  {key:<28} {value}")
        amounts = {k: parsing.parse_printed_amount(summary[k]) for k in AMOUNT_KEYS if k in summary}
        out("")
        out(f"Expected payments total: {processor.expected_credits(amounts)}")
        out(f"Expected purchases total: {processor.expected_debits(amounts)}")
