"""Step 4: money/date parsing and review-row building."""

from datetime import date
from decimal import Decimal

import pytest

from document_intake.extraction.parsing import (
    ParseError,
    build_fields,
    build_lines,
    parse_header,
    parse_iso_date,
    parse_money,
)
from document_intake.models import DocType
from document_intake.tests.fakes import BANK_STATEMENT, RECEIPT
from document_intake.validators.types import Sev


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
    return {row.key: row.value for row in rows}


def test_receipt_fields() -> None:
    rows = build_fields(DocType.RECEIPT, RECEIPT, "CAD")
    values = _by_key(rows)
    assert rows[0].key == "vendor_name"
    assert values["vendor_name"].parsed == "Hardware Store"
    assert values["total"].normalized_value == "113.00"
    assert values["total"].parsed == Decimal("113.00")
    assert values["invoice_date"].parsed == date(2026, 9, 3)
    assert values["invoice_number"].value == ""
    assert values["currency"].normalized_value == "CAD"
    assert "assumed CAD" in values["currency"].warning
    # Reviewer-only fields exist but start blank.
    assert values["posting_date"].value == ""
    assert values["account"].value == ""


def test_unreadable_values_kept_verbatim_and_flagged() -> None:
    data = {**RECEIPT, "total": ">$113.00", "invoice_date": "03/09/2026", "currency": "dollars"}
    values = _by_key(build_fields(DocType.RECEIPT, data, "CAD"))
    assert values["total"].value == ">$113.00"
    assert values["total"].parsed is None
    assert values["total"].normalized_value == ""
    assert values["total"].problem == "unreadable amount"
    assert values["invoice_date"].problem
    assert values["currency"].problem


@pytest.mark.parametrize(
    ("raw", "value", "problem", "warning"),
    [
        ("1234", "1234", False, False),
        ("****1234", "1234", False, False),
        ("12345678901234", "1234", False, True),
        ("12", "12", True, False),
    ],
)
def test_account_number_is_masked(raw, value, problem, warning) -> None:
    parsed = parse_header("account_last4", raw, "CAD")
    assert parsed.value == value
    assert bool(parsed.problem) is problem
    assert bool(parsed.warning) is warning
    assert "5678" not in parsed.value


def test_header_as_input() -> None:
    item = parse_header("total", "12.00", "CAD").as_input("total")
    assert item.key == "total"
    assert item.parsed == Decimal("12.00")
    assert not item.is_blank


def test_statement_lines() -> None:
    lines = build_lines(DocType.BANK_STATEMENT, BANK_STATEMENT)
    assert [line.line_no for line in lines] == [1, 2]
    rent, payment = lines
    assert rent.debit == Decimal("1500.00")
    assert rent.amount == Decimal("-1500.00")
    assert payment.amount == Decimal("700.00")
    assert rent.date == date(2026, 9, 3)
    assert rent.problems == []


def messages(line) -> list[str]:
    return [message for _, message in line.problems]


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
            {"date": "Sept 1", "description": "x", "debit": "??", "credit": None, "balance": None},
        ]
    }
    missing, negative, both, unreadable = build_lines(DocType.BANK_STATEMENT, data)
    assert messages(missing) == [
        "Date is missing.",
        "No debit or credit amount.",
        "Description is missing.",
    ]
    assert (Sev.ERROR, "No debit or credit amount.") in missing.problems
    assert negative.debit is None  # never stored: it would violate the non-negative rule
    assert negative.balance is None
    assert "Debit: negative value." in messages(negative)
    assert "Balance: unreadable amount." in messages(negative)
    assert messages(both) == ["Both debit and credit are filled in."]
    assert both.amount == Decimal("0.00")
    # Unreadable values are reported once, not also as "missing".
    assert messages(unreadable) == ["Date: date is not YYYY-MM-DD.", "Debit: unreadable amount."]


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
    assert line.problems == []
    assert line.amount == Decimal("700.00")


def test_receipt_lines() -> None:
    lines = build_lines(DocType.RECEIPT, RECEIPT)
    assert [line.amount for line in lines] == [Decimal("50.00"), Decimal("50.00")]
    assert lines[0].raw["quantity"] == "2"
    (bad,) = build_lines(DocType.INVOICE, {"line_items": [{"description": "", "amount": None}]})
    assert messages(bad) == ["Amount is missing.", "Description is missing."]
    (unreadable,) = build_lines(
        DocType.INVOICE, {"line_items": [{"description": "a", "amount": "x"}]}
    )
    assert messages(unreadable) == ["Amount: unreadable amount."]


def test_line_as_input() -> None:
    (line,) = build_lines(DocType.RECEIPT, {"line_items": [{"description": "a", "amount": "1"}]})
    item = line.as_input()
    assert item.line_no == 1
    assert item.amount == Decimal("1.00")
    assert item.problems == ()


def test_no_lines_when_model_returns_none() -> None:
    assert build_lines(DocType.BANK_STATEMENT, {"transactions": None}) == []
