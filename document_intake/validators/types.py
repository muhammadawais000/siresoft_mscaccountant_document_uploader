"""Typed inputs and results for validation rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from datetime import date as Date  # noqa: N812 - a field below is called "date"
from decimal import Decimal
from enum import StrEnum


class Sev(StrEnum):
    """Mirrors ``models.Severity`` without importing Django."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


SEVERITY_RANK = {Sev.INFO: 0, Sev.WARNING: 1, Sev.ERROR: 2}


@dataclass(frozen=True)
class Issue:
    rule_code: str
    severity: Sev
    message: str
    field_key: str = ""
    line_no: int | None = None

    @property
    def key(self) -> tuple[str, str, int | None]:
        """Identity used to carry a reviewer's "resolved" flag across re-validation."""
        return (self.rule_code, self.field_key, self.line_no)


@dataclass(frozen=True)
class FieldInput:
    """A header field after parsing.

    ``parsed`` is a Decimal, date or str; None when blank or unreadable.
    ``problem`` is the parser's complaint about a present-but-unreadable value.
    ``warning`` is a parser note that needs a human look (e.g. an assumed currency).
    """

    key: str
    raw: str
    parsed: Decimal | date | str | None
    problem: str = ""
    warning: str = ""

    @property
    def is_blank(self) -> bool:
        return not self.raw.strip()


@dataclass(frozen=True)
class LineInput:
    line_no: int
    date: date | None = None
    description: str = ""
    debit: Decimal | None = None
    credit: Decimal | None = None
    amount: Decimal | None = None
    balance: Decimal | None = None
    tax: Decimal | None = None
    posting_date: Date | None = None
    # (severity, message) found while parsing the model's values for this line.
    problems: tuple[tuple[Sev, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RuleConfig:
    today: date
    tolerance: Decimal = Decimal("0.01")
    expected_tax_rate: Decimal = Decimal("0.13")
    tax_rate_slack: Decimal = Decimal("0.005")
