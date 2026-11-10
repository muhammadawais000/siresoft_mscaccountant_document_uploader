"""Step 5: pure validation rules. No database; inputs are built directly."""

from datetime import date
from decimal import Decimal

import pytest

from document_intake.extraction.parsing import parse_header
from document_intake.validators import FieldInput, LineInput, RuleConfig, derive_statuses, validate
from document_intake.validators.types import Sev

CFG = RuleConfig(today=date(2026, 11, 10))


def fields(**values) -> dict[str, FieldInput]:
    return {k: parse_header(k, v, "CAD").as_input(k) for k, v in values.items()}


def receipt(**overrides) -> dict[str, FieldInput]:
    base = {
        "vendor_name": "Hardware Store",
        "invoice_date": "2026-09-03",
        "currency": "CAD",
        "subtotal": "100.00",
        "hst": "13.00",
        "total": "113.00",
    }
    return fields(**{**base, **overrides})


def statement(**overrides) -> dict[str, FieldInput]:
    base = {
        "statement_period_start": "2026-09-01",
        "statement_period_end": "2026-09-30",
        "currency": "CAD",
        "opening_balance": "5000.00",
        "closing_balance": "4200.00",
    }
    return fields(**{**base, **overrides})


def line(n, day=3, debit=None, credit=None, balance=None, amount=None, desc="x", problems=()):
    return LineInput(
        line_no=n,
        date=date(2026, 9, day) if day else None,
        description=desc,
        debit=Decimal(debit) if debit else None,
        credit=Decimal(credit) if credit else None,
        amount=Decimal(amount) if amount else None,
        balance=Decimal(balance) if balance else None,
        problems=problems,
    )


def codes(issues) -> list[str]:
    return [i.rule_code for i in issues]


def by_code(issues, code):
    return next(i for i in issues if i.rule_code == code)


BANK_LINES = [
    line(1, 3, debit="1500.00", balance="3500.00"),
    line(2, 15, credit="700.00", balance="4200.00"),
]

# ------------------------------------------------------------------ receipts


def test_clean_receipt_has_no_issues() -> None:
    lines = [line(1, amount="60.00"), line(2, amount="40.00")]
    assert validate("receipt", receipt(), lines, CFG) == []


def test_total_mismatch() -> None:
    issues = validate("receipt", receipt(total="114.00"), [], CFG)
    issue = by_code(issues, "total_mismatch")
    assert issue.severity == Sev.ERROR
    assert issue.field_key == "total"
    assert "100.00 + tax 13.00 = 113.00" in issue.message
    assert "difference 1.00" in issue.message


@pytest.mark.parametrize("total", ["113.01", "112.99"])
def test_total_within_tolerance(total) -> None:
    assert "total_mismatch" not in codes(validate("receipt", receipt(total=total), [], CFG))


def test_total_just_outside_tolerance() -> None:
    assert "total_mismatch" in codes(validate("receipt", receipt(total="113.02"), [], CFG))


def test_multiple_taxes_are_summed() -> None:
    data = receipt(hst=None, gst="5.00", pst="8.00", total="113.00")
    assert "total_mismatch" not in codes(validate("receipt", data, [], CFG))


def test_no_tax_means_subtotal_equals_total() -> None:
    data = receipt(hst=None, total="100.00")
    assert codes(validate("receipt", data, [], CFG)) == []
    assert "total_mismatch" in codes(validate("receipt", receipt(hst=None), [], CFG))


def test_unreadable_tax_suppresses_sum_check() -> None:
    issues = validate("receipt", receipt(hst="1E", total="999.00"), [], CFG)
    assert "unreadable_value" in codes(issues)
    assert "total_mismatch" not in codes(issues)


def test_missing_subtotal_skips_total_check() -> None:
    issues = validate("receipt", receipt(subtotal=None, total="1.00"), [], CFG)
    assert "total_mismatch" not in codes(issues)


def test_line_sum_mismatch() -> None:
    lines = [line(1, amount="60.00"), line(2, amount="30.00")]
    issue = by_code(validate("receipt", receipt(), lines, CFG), "line_sum_mismatch")
    assert issue.field_key == "subtotal"
    assert "add up to 90.00" in issue.message


def test_line_sum_with_missing_amount_is_unverified() -> None:
    lines = [line(1, amount="60.00"), line(2, amount=None)]
    issues = validate("receipt", receipt(), lines, CFG)
    assert by_code(issues, "line_sum_unverified").severity == Sev.WARNING
    assert "line_sum_mismatch" not in codes(issues)


def test_negative_line_items_count() -> None:
    lines = [line(1, amount="110.00"), line(2, amount="-10.00")]  # discount line
    assert codes(validate("receipt", receipt(), lines, CFG)) == []


@pytest.mark.parametrize(
    ("hst", "total", "flagged"),
    [
        ("13.00", "113.00", False),
        ("13.40", "113.40", False),  # 13.4% within 0.5 points
        ("5.00", "105.00", True),  # 5% GST only (e.g. Alberta)
        ("15.00", "115.00", True),
    ],
)
def test_tax_rate_plausibility_is_warning_only(hst, total, flagged) -> None:
    issues = validate("receipt", receipt(hst=hst, total=total), [], CFG)
    assert ("tax_rate_unusual" in codes(issues)) is flagged
    if flagged:
        assert by_code(issues, "tax_rate_unusual").severity == Sev.WARNING


def test_zero_tax_is_not_a_rate_problem() -> None:
    assert codes(validate("receipt", receipt(hst="0.00", total="100.00"), [], CFG)) == []


def test_future_date_is_error() -> None:
    issue = by_code(
        validate("receipt", receipt(invoice_date="2026-11-11"), [], CFG), "date_in_future"
    )
    assert issue.severity == Sev.ERROR
    assert issue.field_key == "invoice_date"


def test_today_is_not_future() -> None:
    assert codes(validate("receipt", receipt(invoice_date="2026-11-10"), [], CFG)) == []


def test_due_before_invoice() -> None:
    issues = validate("invoice", receipt(invoice_number="A1", due_date="2026-09-01"), [], CFG)
    assert codes(issues) == ["due_before_invoice"]


@pytest.mark.parametrize("key", ["vendor_name", "invoice_date", "total"])
def test_required_receipt_fields(key) -> None:
    issue = by_code(validate("receipt", receipt(**{key: None}), [], CFG), "required_missing")
    assert issue.field_key == key
    assert issue.severity == Sev.ERROR


def test_invoice_number_required_as_warning_on_invoices_only() -> None:
    assert "required_missing" not in codes(validate("receipt", receipt(), [], CFG))
    issue = by_code(validate("invoice", receipt(), [], CFG), "required_missing")
    assert (issue.field_key, issue.severity) == ("invoice_number", Sev.WARNING)


def test_negative_total_warns() -> None:
    data = receipt(subtotal="-100.00", hst="-13.00", total="-113.00")
    issues = validate("receipt", data, [], CFG)
    assert codes(issues) == ["negative_total"]


def test_assumed_currency_is_a_warning() -> None:
    issue = by_code(validate("receipt", receipt(currency=None), [], CFG), "value_note")
    assert issue.field_key == "currency"
    assert issue.severity == Sev.WARNING


def test_parse_problem_on_line_becomes_issue() -> None:
    lines = [line(1, amount="100.00", problems=((Sev.ERROR, "Tax: unreadable amount."),))]
    issue = by_code(validate("receipt", receipt(), lines, CFG), "line_value")
    assert (issue.line_no, issue.severity) == (1, Sev.ERROR)


# --------------------------------------------------------------- statements


def test_clean_bank_statement() -> None:
    assert validate("bank_statement", statement(), BANK_LINES, CFG) == []


def test_bank_balance_mismatch() -> None:
    issues = validate("bank_statement", statement(closing_balance="4300.00"), BANK_LINES, CFG)
    issue = by_code(issues, "balance_mismatch")
    assert issue.severity == Sev.ERROR
    assert "gives 4,200.00" in issue.message
    assert "difference 100.00" in issue.message


def test_credit_card_signs_are_reversed() -> None:
    # Owed 500; bought 200 (debit); paid 300 (credit) -> owe 400.
    data = statement(opening_balance="500.00", closing_balance="400.00")
    lines = [
        line(1, 5, debit="200.00", balance="700.00"),
        line(2, 9, credit="300.00", balance="400.00"),
    ]
    assert validate("credit_card_statement", data, lines, CFG) == []
    assert "balance_mismatch" in codes(validate("bank_statement", data, lines, CFG))


def test_empty_statement_must_have_equal_balances() -> None:
    assert validate("bank_statement", statement(closing_balance="5000.00"), [], CFG) == []
    assert "balance_mismatch" in codes(validate("bank_statement", statement(), [], CFG))


def test_balance_unverified_when_a_line_has_no_amount() -> None:
    lines = [*BANK_LINES, line(3, 20)]
    issues = validate("bank_statement", statement(), lines, CFG)
    assert by_code(issues, "balance_unverified").severity == Sev.WARNING
    assert "balance_mismatch" not in codes(issues)


def test_running_balance_mismatch_reported_once() -> None:
    lines = [
        line(1, 3, debit="1500.00", balance="3600.00"),  # misread: should be 3500
        line(2, 15, credit="700.00", balance="4300.00"),  # consistent with the printed 3600
        line(3, 20, debit="100.00", balance="4200.00"),
    ]
    issues = validate("bank_statement", statement(), lines, CFG)
    running = [i for i in issues if i.rule_code == "running_balance_mismatch"]
    assert [i.line_no for i in running] == [1]
    assert "Expected balance 3,500.00" in running[0].message


def test_running_balance_skips_lines_without_balance() -> None:
    lines = [line(1, 3, debit="1500.00"), line(2, 15, credit="700.00", balance="4200.00")]
    assert validate("bank_statement", statement(), lines, CFG) == []


def test_running_balance_without_opening() -> None:
    data = statement(opening_balance=None)
    issues = validate("bank_statement", data, BANK_LINES, CFG)
    assert "running_balance_mismatch" not in codes(issues)
    assert by_code(issues, "required_missing").field_key == "opening_balance"


def test_transaction_outside_period() -> None:
    lines = [
        line(1, 3, debit="1500.00"),
        LineInput(2, date(2026, 10, 2), "x", credit=Decimal("700.00")),
    ]
    issue = by_code(validate("bank_statement", statement(), lines, CFG), "date_outside_period")
    assert (issue.line_no, issue.severity) == (2, Sev.WARNING)


def test_transaction_in_future() -> None:
    lines = [*BANK_LINES[:1], LineInput(2, date(2026, 12, 1), "x", credit=Decimal("700.00"))]
    data = statement(statement_period_end="2026-12-31")
    issues = validate("bank_statement", data, lines, CFG)
    future = {(i.field_key, i.line_no) for i in issues if i.rule_code == "date_in_future"}
    assert future == {("statement_period_end", None), ("", 2)}


def test_period_reversed() -> None:
    data = statement(statement_period_start="2026-10-01", statement_period_end="2026-09-01")
    issue = by_code(validate("bank_statement", data, BANK_LINES, CFG), "period_reversed")
    assert issue.severity == Sev.ERROR


def test_statement_required_fields() -> None:
    data = statement(closing_balance=None, statement_period_end=None)
    issues = [
        i
        for i in validate("bank_statement", data, BANK_LINES, CFG)
        if i.rule_code == "required_missing"
    ]
    assert {(i.field_key, i.severity) for i in issues} == {
        ("closing_balance", Sev.ERROR),
        ("statement_period_end", Sev.WARNING),
    }


def test_issues_sorted_most_severe_first() -> None:
    data = statement(closing_balance="1.00", statement_period_end=None)
    severities = [i.severity for i in validate("bank_statement", data, BANK_LINES, CFG)]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warning": 1}[s])


def test_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="No validation rules"):
        validate("payslip", {}, [], CFG)


def test_decimal_precision_no_float_drift() -> None:
    # 0.1 + 0.2 style sums must balance exactly.
    data = statement(opening_balance="0.00", closing_balance="0.30")
    lines = [line(1, 3, credit="0.10"), line(2, 4, credit="0.20")]
    assert validate("bank_statement", data, lines, CFG) == []


# ------------------------------------------------------------------ statuses


def test_derive_statuses() -> None:
    data = receipt(total="114.00", invoice_date=None, currency=None, account=None)
    lines = [
        line(1, amount="100.00"),
        line(2, amount=None, problems=((Sev.ERROR, "Amount is missing."),)),
    ]
    issues = validate("receipt", data, lines, CFG)
    field_status, line_status = derive_statuses(
        data, lines, issues, reviewer_keys=frozenset({"account"})
    )

    assert field_status["vendor_name"].status == "ok"
    assert field_status["total"].status == "error"
    assert "difference" in field_status["total"].reason
    assert field_status["invoice_date"].status == "missing"
    assert field_status["invoice_date"].reason == "Required value is missing."
    assert field_status["currency"].status == "warning"
    assert field_status["account"].status == "ok"  # reviewer field, blank is normal
    assert line_status[1].status == "ok"
    assert line_status[2].status == "error"


def test_derive_statuses_keeps_worst() -> None:
    data = receipt(hst="5.00", total="999.00")  # warning (rate) and error (total) both
    issues = validate("receipt", data, [], CFG)
    field_status, _ = derive_statuses(data, [], issues)
    assert field_status["total"].status == "error"
    assert field_status["hst"].status == "warning"
