"""Shared rule helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal

from .types import FieldInput, Issue, LineInput, Sev

ZERO = Decimal("0.00")


def money(fields: Mapping[str, FieldInput], key: str) -> Decimal | None:
    item = fields.get(key)
    return item.parsed if item and isinstance(item.parsed, Decimal) else None


def day(fields: Mapping[str, FieldInput], key: str) -> date | None:
    item = fields.get(key)
    return item.parsed if item and isinstance(item.parsed, date) else None


def fmt(amount: Decimal) -> str:
    """1234.5 -> '1,234.50'; used only in issue messages shown to the reviewer."""
    return f"{amount:,.2f}"


def close(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


def field_problems(fields: Mapping[str, FieldInput], required: Mapping[str, Sev]) -> list[Issue]:
    """Unreadable values (error) and blank required fields (per-field severity)."""
    issues = []
    for key, item in fields.items():
        if item.problem:
            issues.append(
                Issue("unreadable_value", Sev.ERROR, f"Could not read value: {item.problem}.", key)
            )
        if item.warning:
            issues.append(Issue("value_note", Sev.WARNING, item.warning, key))
    for key, severity in required.items():
        required_item = fields.get(key)
        if required_item is None or required_item.is_blank:
            issues.append(Issue("required_missing", severity, "Required value is missing.", key))
    return issues


def line_problems(lines: Iterable[LineInput]) -> list[Issue]:
    return [
        Issue("line_value", severity, message, line_no=line.line_no)
        for line in lines
        for severity, message in line.problems
    ]


def future_date(item: date | None, key: str, today: date) -> list[Issue]:
    if item is not None and item > today:
        return [Issue("date_in_future", Sev.ERROR, "Date is in the future.", key)]
    return []
