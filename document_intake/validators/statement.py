"""Rules for bank and credit-card statements.

Sign convention per line: ``debit`` and ``credit`` are non-negative column amounts.
Bank account:  balance moves +credit -debit (money in raises the balance).
Credit card:   balance moves +debit -credit (purchases raise the amount owed).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from .common import ZERO, close, day, field_problems, fmt, future_date, line_problems, money
from .types import FieldInput, Issue, LineInput, RuleConfig, Sev

REQUIRED = {
    "opening_balance": Sev.ERROR,
    "closing_balance": Sev.ERROR,
    "statement_period_start": Sev.WARNING,
    "statement_period_end": Sev.WARNING,
}


def movement(line: LineInput, *, credit_card: bool) -> Decimal | None:
    """Change in balance caused by one line; None if neither column is known."""
    if line.debit is None and line.credit is None:
        return None
    debit, credit = line.debit or ZERO, line.credit or ZERO
    return debit - credit if credit_card else credit - debit


def check_balance_equation(
    fields: Mapping[str, FieldInput],
    lines: Sequence[LineInput],
    cfg: RuleConfig,
    *,
    credit_card: bool,
) -> list[Issue]:
    """opening + movements == closing."""
    opening, closing = money(fields, "opening_balance"), money(fields, "closing_balance")
    if opening is None or closing is None:
        return []
    moves = [movement(line, credit_card=credit_card) for line in lines]
    if any(m is None for m in moves):
        return [
            Issue(
                "balance_unverified",
                Sev.WARNING,
                "Some transactions have no amount, so the closing balance cannot be checked.",
                "closing_balance",
            )
        ]
    expected = opening + sum((m for m in moves if m is not None), ZERO)
    if close(expected, closing, cfg.tolerance):
        return []
    in_word, out_word = ("payments", "charges") if credit_card else ("credits", "debits")
    return [
        Issue(
            "balance_mismatch",
            Sev.ERROR,
            f"Opening {fmt(opening)} with {in_word} and {out_word} gives {fmt(expected)}, "
            f"but closing balance is {fmt(closing)} (difference {fmt(closing - expected)}).",
            "closing_balance",
        )
    ]


def check_running_balance(
    fields: Mapping[str, FieldInput],
    lines: Sequence[LineInput],
    cfg: RuleConfig,
    *,
    credit_card: bool,
) -> list[Issue]:
    """Each printed line balance should follow from the previous one.

    After a mismatch we continue from the printed balance, so one misread line produces
    one issue instead of a cascade.
    """
    issues: list[Issue] = []
    expected: Decimal | None = money(fields, "opening_balance")
    for line in lines:
        move = movement(line, credit_card=credit_card)
        projected = expected + move if expected is not None and move is not None else None
        if line.balance is None:
            expected = projected
            continue
        if projected is not None and not close(projected, line.balance, cfg.tolerance):
            issues.append(
                Issue(
                    "running_balance_mismatch",
                    Sev.ERROR,
                    f"Expected balance {fmt(projected)} after this line, "
                    f"statement shows {fmt(line.balance)}.",
                    line_no=line.line_no,
                )
            )
        expected = line.balance
    return issues


def check_period(
    fields: Mapping[str, FieldInput], lines: Sequence[LineInput], cfg: RuleConfig
) -> list[Issue]:
    """Dates against the period. Card statements are cut by posting date, so a purchase made
    just before the period but posted inside it is normal."""
    start, end = day(fields, "statement_period_start"), day(fields, "statement_period_end")
    issues = future_date(end, "statement_period_end", cfg.today)
    if start and end and start > end:
        issues.append(
            Issue(
                "period_reversed",
                Sev.ERROR,
                "Statement period starts after it ends.",
                "statement_period_start",
            )
        )
        return issues
    for line in lines:
        posted = line.posting_date or line.date
        if posted is None:
            continue
        if posted > cfg.today or (line.date is not None and line.date > cfg.today):
            issues.append(
                Issue(
                    "date_in_future",
                    Sev.ERROR,
                    "Transaction date is in the future.",
                    line_no=line.line_no,
                )
            )
        elif (start and posted < start) or (end and posted > end):
            issues.append(
                Issue(
                    "date_outside_period",
                    Sev.WARNING,
                    "Transaction date is outside the statement period.",
                    line_no=line.line_no,
                )
            )
    return issues


def validate_statement(
    fields: Mapping[str, FieldInput],
    lines: Sequence[LineInput],
    cfg: RuleConfig,
    *,
    credit_card: bool = False,
    check_balance: bool = True,
) -> list[Issue]:
    """``check_balance=False``: the caller checks the closing balance itself (card report)."""
    balance = (
        check_balance_equation(fields, lines, cfg, credit_card=credit_card) if check_balance else []
    )
    return [
        *field_problems(fields, REQUIRED),
        *line_problems(lines),
        *balance,
        *check_running_balance(fields, lines, cfg, credit_card=credit_card),
        *check_period(fields, lines, cfg),
    ]
