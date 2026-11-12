"""Small template filters for the intake pages."""

from __future__ import annotations

from decimal import Decimal

from django import template

register = template.Library()

STATUS_TONES = {
    "uploaded": "info",
    "processing": "info",
    "needs_review": "warn",
    "needs_clarification": "warn",
    "approved": "ok",
    "exported": "ok",
    "failed": "err",
}


@register.filter
def tone(status: str) -> str:
    """Badge colour for a document status: ``{{ doc.status|tone }}``."""
    return STATUS_TONES.get(status, "neutral")


@register.filter
def money(value: Decimal | None) -> str:
    """1234.5 -> "1,234.50", negatives in brackets, None -> "—"."""
    if value is None:
        return "—"
    text = f"{abs(value):,.2f}"
    return f"({text})" if value < 0 else text


@register.filter
def plain(value: object) -> str:
    """Editable cell value: None -> "", Decimal/date -> str."""
    return "" if value is None else str(value)
