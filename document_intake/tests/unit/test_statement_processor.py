"""Card statements: parsing, classification, totals and checks, without any model."""

from datetime import date
from decimal import Decimal

import pytest

from document_intake.extraction import statement_processor as sp
from document_intake.extraction.parsing import (
    ParseError,
    build_lines,
    parse_printed_amount,
    parse_printed_date,
)
from document_intake.tests.card_fixtures import BMO_PAGES, BMO_TEXT, TD_PAGES, TD_TEXT, row

D = Decimal


def pages_of(text):
    return ["\n".join(lines) for lines in text]


def merged(answers, text):
    parsed = [(n, sp.PrintedPage.model_validate(a)) for n, a in answers.items()]
    return sp.merge_pages(parsed, pages_of(text))


def card_rows(data):
    lines = build_lines("credit_card_statement", data)
    return [
        sp.CardRow(
            line_no=x.line_no,
            date=x.date,
            description=x.description,
            card_last4=x.card_last4,
            pre_tax=x.pre_tax,
            tax=x.tax,
            amount=None if x.amount is None else -x.amount,
        )
        for x in lines
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-$2,000.00", "-2000.00"),
        ("$ -613.03", "-613.03"),
        ("$ -6,683.23", "-6683.23"),
        ("\N{MINUS SIGN}$5.00", "-5.00"),
        ("1.72 (e)", "1.72"),
        ("12.00 CR", "-12.00"),
        ("$1,025.90", "1025.90"),
        ("(12.00)", "-12.00"),
    ],
)
def test_printed_amounts(text, expected) -> None:
    assert parse_printed_amount(text) == D(expected)


@pytest.mark.parametrize("text", ["-$2,000.00 CashAdvances $0.00", "-5.00 CR", 613.03, "abc"])
def test_unclear_amounts_are_refused(text) -> None:
    with pytest.raises(ParseError):
        parse_printed_amount(text)


@pytest.mark.parametrize(
    ("text", "reference", "expected"),
    [
        ("12/03", date(2026, 1, 3), date(2025, 12, 3)),
        ("01/02", date(2026, 1, 3), date(2026, 1, 2)),
        ("OCT6", date(2025, 11, 5), date(2025, 10, 6)),
        ("Nov 3", date(2025, 11, 5), date(2025, 11, 3)),
        ("2026-12-03", date(2026, 1, 3), date(2025, 12, 3)),  # a year the model made up
        ("October 7, 2025", None, date(2025, 10, 7)),
    ],
)
def test_row_dates_get_their_year_from_the_statement(text, reference, expected) -> None:
    assert parse_printed_date(text, reference) == expected


@pytest.mark.parametrize("text", ["12/03", "13/45", "soon", "FOO 12"])
def test_year_is_never_guessed(text) -> None:
    with pytest.raises(ParseError):
        parse_printed_date(text, None)


def test_sign_decides_and_wording_only_warns() -> None:
    assert sp.classify(D("-0.01")) == "credit"
    assert sp.classify(D("0.01")) == "debit"
    assert sp.classify(None) is None
    [line] = build_lines(
        "credit_card_statement",
        {"transactions": [row("12/03", "12/04", "PAYMENT THANK YOU", "$ 50.00")]},
    )
    assert line.debit == D("50.00")
    assert line.credit is None
    assert any("payment or refund" in message for _, message in line.problems)


def test_negative_amount_is_a_credit_not_an_empty_row() -> None:
    [line] = build_lines(
        "credit_card_statement",
        {"transactions": [row("12/03", "12/04", "CASHBACK", "$ -613.03")]},
    )
    assert (line.debit, line.credit) == (None, D("613.03"))
    assert line.amount == D("613.03")  # older meaning kept: credit minus debit


def test_printed_text_wins_over_the_models_number() -> None:
    item = row("12/03", "12/04", "CASHBACK", "$ -613.03", amount="613.03")
    [line] = build_lines("credit_card_statement", {"transactions": [item]})
    assert line.credit == D("613.03")
    assert any("printed used" in message for _, message in line.problems)


@pytest.mark.parametrize(
    ("text", "dated", "expected"),
    [
        ("PREVIOUSSTATEMENTBALANCE", True, True),
        ("PREVIOUS STATEMENT BALANCE", False, True),
        ("TOTAL NEW BALANCE", False, True),
        ("Sub-total", False, True),
        ("TOTAL CREDITS xxxx-xxxx-xxxx-6915", False, True),
        ("NEW BALANCE", False, True),
        ("NEW BALANCE", True, False),  # the shoe shop
        ("NEW BALANCE TORONTO", True, False),
        ("TOTAL WINE", True, False),
    ],
)
def test_non_transactions(text, dated, expected) -> None:
    assert sp.is_non_transaction(text, dated=dated) is expected


def test_row_count_reads_the_pdf_text() -> None:
    assert sp.count_rows(pages_of(BMO_TEXT)) == 27
    assert sp.count_rows(pages_of(TD_TEXT)) == 40
    assert sp.count_rows(["OCT 7 OCT 8 A $1.00\nOCT 9 OCT 10 B $2.00"]) == 2
    assert sp.count_rows(["2026-09-04 Office supplies 212.43"]) is None
    assert sp.count_rows([]) is None


def test_tables_repeated_by_pdfplumber_are_not_counted() -> None:
    raw = "--- Page 1 ---\n12/03 12/04 A $ 1.00\n\n[Tables]\n12/03 12/04 A | $ 1.00"
    assert sp.count_rows(sp.split_pages(raw)) == 1


@pytest.mark.parametrize(
    ("line", "amounts"),
    [
        ("12/04 12/08 SHOP $ 13.26 $ 1.72 (e) $ 14.98", ["13.26", "1.72", "14.98"]),
        ("12/03 12/04 CASHBACK $ -613.03 $ 0.00 $ -613.03", ["-613.03", "0.00", "-613.03"]),
        ("OCT14 OCT15 PAYMENT -$2,000.00 CashAdvances $0.00", ["-2000.00", "0.00"]),
        ("OCT14 OCT15 PAYMENT-THANKYOU-$2,000.00", ["-2000.00"]),
        ("OCT6 OCT7 STORE905-738-3663 $1,454.92", ["1454.92"]),
        ("Dec 27 Dec 30 BMW TORONTO -18.00", ["-18.00"]),
        ("Jan 06 Jan 07 APPLE.COM/BILL 866-712-7753 ON 4.51", ["4.51"]),
        ("12/05 12/08 HMART - RICHMOND HILL $ 5.49", ["5.49"]),
        ("Jan 06 Jan 07 REFUND 12.00 CR", ["-12.00"]),
    ],
)
def test_amounts_on_a_printed_line_keep_their_sign(line, amounts) -> None:
    assert sp.TextRow(1, line, "").amounts == [D(x) for x in amounts]


def test_row_not_in_the_text_is_flagged() -> None:
    answers = {1: {**BMO_PAGES[1], "transactions": [row("12/04", "12/08", "GHOST", "$ 99.99")]}}
    data = merged(answers, BMO_TEXT)
    [line] = build_lines("credit_card_statement", data)
    assert any("not found in the PDF text" in message for _, message in line.problems)


def test_purchase_and_its_refund_on_the_same_dates_keep_their_signs() -> None:
    text = [["Jan 22 Jan 23 EBAY 67.44", "Jan 22 Jan 23 EBAY -67.44"]]
    items = [row("Jan 22", "Jan 23", "EBAY", "67.44"), row("Jan 22", "Jan 23", "EBAY", "67.44")]
    data = merged({1: {**BMO_PAGES[2], "transactions": items}}, text)
    assert [r["text_amount"] for r in data["transactions"]] == ["67.44", "-67.44"]


def test_sections_continue_across_pages() -> None:
    layout = sp.read_layout(pages_of(BMO_TEXT))
    assert layout.first_card == {1: "", 2: "3664", 3: "5124", 4: "5124", 5: "5124"}
    assert layout.footers == {
        "6915": {"credits": D("7296.26"), "debits": D("0.00")},
        "3664": {"credits": D("0.00"), "debits": D("6650.52")},
        "5124": {"credits": D("0.00"), "debits": D("337.89")},
    }


@pytest.mark.parametrize(
    ("line", "card"),
    [
        ("Card Number xxxx-xxxx-xxxx-6915 1234567 ONTARIO INC", "6915"),
        ("Card number 4500 XXXX XXXX 7104", "7104"),
        ("Card Number: **** 8054", "8054"),
    ],
)
def test_card_header_is_not_confused_by_a_numbered_company(line, card) -> None:
    assert sp.read_layout([f"{line}\n12/03 12/04 SHOP $ 1.00"]).rows[0].card_last4 == card


def test_only_pages_without_transactions_are_skipped() -> None:
    for text, expected in ((BMO_TEXT, [1, 2]), (TD_TEXT, [1, 3])):
        kept = [n for n, p in enumerate(pages_of(text), 1) if sp.needs_model(p, n)]
        assert kept == expected
    assert sp.needs_model("CUSTOMER SERVICE\n12/03 12/04 SHOP $ 5.00", 4)
    assert sp.needs_model("CUSTOMER SERVICE\nOct 3 SHOP 5.00", 4)


def test_bmo_numbers() -> None:
    data = merged(BMO_PAGES, BMO_TEXT)
    rows = card_rows(data)
    debits, credits, unknown = sp.split_rows(rows)
    totals = sp.compute_totals(rows)
    assert (len(rows), len(debits), len(credits), len(unknown)) == (27, 25, 2, 0)
    assert totals.total_credits == D("7296.26")
    assert totals.total_debits == D("6988.41")
    assert totals.total_pretax == D("6184.43")
    assert totals.total_tax == D("803.98")
    assert sp.calculated_closing(D("7296.26"), totals) == D("6988.41")
    assert {r.card_last4 for r in credits} == {"6915"}
    per_card = {
        c: sp.compute_totals(r for r in rows if r.card_last4 == c) for c in ("3664", "5124", "6915")
    }
    assert per_card["3664"].total_debits == D("6650.52")
    assert per_card["5124"].total_debits == D("337.89")
    assert per_card["6915"].total_debits == D("0.00")
    assert rows[0].date == date(2025, 12, 3)
    assert next(r for r in rows if r.description.startswith("SHOP 24")).date == date(2026, 1, 2)


def test_td_numbers() -> None:
    data = merged(TD_PAGES, TD_TEXT)
    rows = card_rows(data)
    _, credits, _ = sp.split_rows(rows)
    totals = sp.compute_totals(rows)
    assert len(rows) == 40
    assert [-r.amount for r in credits] == [
        D(x) for x in ("2000", "4000", "2000", "4000", "3000", "5731", "1771.90")
    ]
    assert totals.total_credits == D("22502.90")
    assert totals.total_debits == D("21489.43")
    assert (totals.total_pretax, totals.total_tax) == (None, None)
    assert sp.calculated_closing(D("20730.89"), totals) == D("19717.42")
    assert [r["description"] for r in data["removed_rows"]] == [
        "PREVIOUSSTATEMENTBALANCE",
        "TOTAL NEW BALANCE",
    ]
    assert (data["statement_period_start"], data["statement_period_end"]) == (
        "2025-10-07",
        "2025-11-05",
    )
    assert rows[0].date == date(2025, 10, 6)
    assert {r.card_last4 for r in rows} == {"8054"}


def check(code, rows, **kwargs):
    defaults = {
        "previous": D("100.00"),
        "printed_closing": D("110.00"),
        "summary": {},
        "sections": [],
        "text_rows": len(rows),
    }
    return next(c for c in sp.run_checks(rows, **{**defaults, **kwargs}) if c.code == code)


def simple(*amounts):
    return [
        sp.CardRow(line_no=i, amount=None if a is None else D(a), card_last4="1111")
        for i, a in enumerate(amounts, 1)
    ]


def test_closing_check_is_strict_to_the_cent() -> None:
    assert check("closing_ok", simple("10.00")).ok is True
    off = check("closing_ok", simple("10.01"))
    assert off.ok is False
    assert "difference -0.01" in off.message


def test_missing_amount_is_never_a_silent_balance() -> None:
    rows = simple("10.00", None)
    assert check("rows_ok", rows).ok is False
    assert "line 2" in check("rows_ok", rows).message
    closing = check("closing_ok", rows)
    assert closing.ok is None
    assert closing.failed


def test_rows_check_ignores_the_models_count() -> None:
    assert check("rows_ok", simple("10.00"), text_rows=2).ok is False
    unknown = check("rows_ok", simple("10.00"), text_rows=None)
    assert unknown.ok is None
    assert unknown.failed


def test_cash_advance_counts_by_its_sign() -> None:
    cashback = {"payments": D("-50.00"), "cash_advances": D("-20.00")}
    assert sp.expected_credits(cashback) == D("70.00")
    advance = {"net_purchases": D("10.00"), "payments": D("-50.00"), "cash_advances": D("20.00")}
    assert sp.expected_credits(advance) == D("50.00")
    assert sp.expected_debits({**advance, "fees": D("5.00"), "interest": D("1.00")}) == D("36.00")


def test_unprinted_totals_do_not_fail() -> None:
    for code in ("debits_ok", "credits_ok", "section_ok"):
        result = check(code, simple("10.00"))
        assert result.ok is None
        assert not result.failed


def test_section_check_names_the_card() -> None:
    sections = [{"card_last4": "1111", "total_credits": "0.00", "total_debits": "12.00"}]
    result = check("section_ok", simple("10.00"), sections=sections)
    assert result.ok is False
    assert "card 1111 debits 10.00, printed 12.00" in result.message


@pytest.mark.parametrize(
    ("value", "text", "expected"),
    [
        ("01/03/2026", "Statement Date (MM/DD/YYYY)", "2026-01-03"),
        ("25/12/2025", "", "2025-12-25"),
        ("01/03/2026", "", "01/03/2026"),  # ambiguous: kept as printed
        ("November05,2025", "", "2025-11-05"),
        ("", "", None),
    ],
)
def test_header_dates(value, text, expected) -> None:
    assert sp._header_date(value, text) == expected
