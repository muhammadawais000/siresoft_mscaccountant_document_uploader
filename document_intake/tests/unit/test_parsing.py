"""Step 4: money/date parsing and review-row building."""

from datetime import date
from decimal import Decimal

import pytest

from document_intake.extraction.parsing import (
    ParseError,
    build_fields,
    build_lines,
    parse_iso_date,
    parse_money,
)
from document_intake.models import DocType, FieldStatus
from document_intake.tests.fakes import BANK_STATEMENT, RECEIPT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234.56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("1,234,567.00", Decimal("1234567.00")),
        ("$12.40", Decimal("12.40")),
        ("CAD 12.40", Decimal("12.40")),
        ("12", Decimal("12.00")),
        ("0.1", Decimal("0.10")),
        ("-50.00", Decimal("-50.00")),
        ("(50.00)", Decimal("-50.00")),
        ("50.00-", Decimal("-50.00")),
        ("12.500", Decimal("12.50")),  # trailing zeros are exact, not rounding
        (7, Decimal("7.00")),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_parse_money_valid(raw, expected) -> None:
    result = parse_money(raw)
    assert result == expected
    if result is not None:
        assert result.as_tuple().exponent == -2


@pytest.mark.parametrize(
    "raw",
    [
        ">$12.40",
        "12.345",
        "1.234,56",
        "12,34",
        "abc",
        "--5",
        "(-5.00)",
        "1e3",
        12.4,
        True,
        "12 .4x",
    ],
)
def test_parse_money_rejects(raw) -> None:
    with pytest.raises(ParseError):
        parse_money(raw)


def test_parse_money_never_uses_float() -> None:
    assert parse_money("0.10") + parse_money("0.20") == Decimal("0.30")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-09-03", date(2026, 9, 3)), (None, None), ("", None)],
)
def test_parse_date_valid(raw, expected) -> None:
    assert parse_iso_date(raw) == expected


@pytest.mark.parametrize("raw", ["09/03/2026", "2026-02-30", "Sept 3", "2026-9-3"])
def test_parse_date_rejects(raw) -> None:
    with pytest.raises(ParseError):
        parse_iso_date(raw)


def _by_key(rows):
    return {row.key: row for row in rows}


def test_receipt_fields() -> None:
    rows = _by_key(build_fields(DocType.RECEIPT, RECEIPT, "CAD"))
    assert rows["vendor_name"].status == FieldStatus.OK
    assert rows["total"].normalized_value == "113.00"
    assert rows["invoice_date"].normalized_value == "2026-09-03"
    assert rows["invoice_number"].status == FieldStatus.MISSING
    assert rows["currency"].status == FieldStatus.WARNING
    assert rows["currency"].normalized_value == "CAD"
    assert next(r.key for r in build_fields(DocType.RECEIPT, RECEIPT, "CAD")) == "vendor_name"


def test_unreadable_values_kept_verbatim_and_flagged() -> None:
    data = {**RECEIPT, "total": ">$113.00", "invoice_date": "03/09/2026", "currency": "dollars"}
    rows = _by_key(build_fields(DocType.RECEIPT, data, "CAD"))
    assert rows["total"].status == FieldStatus.ERROR
    assert rows["total"].value == ">$113.00"
    assert rows["total"].normalized_value == ""
    assert rows["invoice_date"].status == FieldStatus.ERROR
    assert rows["currency"].status == FieldStatus.ERROR


@pytest.mark.parametrize(
    ("raw", "value", "status"),
    [
        ("1234", "1234", FieldStatus.OK),
        ("****1234", "1234", FieldStatus.OK),
        ("12345678901234", "1234", FieldStatus.WARNING),
        ("12", "12", FieldStatus.ERROR),
    ],
)
def test_account_number_is_masked(raw, value, status) -> None:
    rows = _by_key(build_fields(DocType.BANK_STATEMENT, {"account_last4": raw}, "CAD"))
    assert rows["account_last4"].value == value
    assert rows["account_last4"].status == status


def test_statement_lines() -> None:
    lines = build_lines(DocType.BANK_STATEMENT, BANK_STATEMENT)
    assert [line.line_no for line in lines] == [1, 2]
    rent, payment = lines
    assert rent.debit == Decimal("1500.00")
    assert rent.amount == Decimal("-1500.00")
    assert payment.amount == Decimal("700.00")
    assert rent.date == date(2026, 9, 3)
    assert rent.status == FieldStatus.OK


def test_statement_line_problems() -> None:
    data = {
        "transactions": [
            {"date": None, "description": "", "debit": None, "credit": None, "balance": None},
            {
                "date": "2026-09-01",
                "description": "x",
                "debit": "-5.00",
                "credit": None,
                "balance": "bad",
            },
            {
                "date": "2026-09-01",
                "description": "x",
                "debit": "5.00",
                "credit": "5.00",
                "balance": None,
            },
        ]
    }
    missing, negative, both = build_lines(DocType.BANK_STATEMENT, data)
    assert missing.status == FieldStatus.MISSING
    assert "no debit or credit" in missing.status_reason
    assert negative.status == FieldStatus.ERROR
    assert negative.debit is None  # never stored: it would violate the non-negative rule
    assert negative.balance is None
    assert both.status == FieldStatus.WARNING
    assert both.amount == Decimal("0.00")


def test_zero_in_other_column_is_not_a_conflict() -> None:
    data = {
        "transactions": [
            {
                "date": "2026-09-15",
                "description": "Deposit",
                "debit": "0.00",
                "credit": "700.00",
                "balance": None,
            }
        ]
    }
    (line,) = build_lines(DocType.BANK_STATEMENT, data)
    assert line.status == FieldStatus.OK
    assert line.amount == Decimal("700.00")


def test_receipt_lines() -> None:
    lines = build_lines(DocType.RECEIPT, RECEIPT)
    assert [line.amount for line in lines] == [Decimal("50.00"), Decimal("50.00")]
    assert lines[0].raw["quantity"] == "2"
    bad = build_lines(DocType.INVOICE, {"line_items": [{"description": "", "amount": None}]})
    assert bad[0].status == FieldStatus.MISSING


def test_no_lines_when_model_returns_none() -> None:
    assert build_lines(DocType.BANK_STATEMENT, {"transactions": None}) == []
