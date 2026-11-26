"""Card statements end to end: PDF -> per-page answers -> report, API, page and edits."""

import copy
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from document_intake.exceptions import MalformedModelOutput
from document_intake.models import DocType, DocumentStatus
from document_intake.services import lifecycle
from document_intake.services.extraction import run_extraction
from document_intake.tests.card_fixtures import (
    BMO_PAGES,
    BMO_TEXT,
    TD_PAGES,
    TD_TEXT,
    PageProvider,
    make_pdf,
    page,
)
from document_intake.tests.conftest import make_image
from document_intake.tests.factories import DocumentFactory

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}
TEXT = {"bmo": BMO_TEXT, "td": TD_TEXT}
PAGES = {"bmo": BMO_PAGES, "td": TD_PAGES}


def extract(user, name, change=None, missed=None):
    pages = copy.deepcopy(PAGES[name])
    if change:
        change(pages)
    doc = DocumentFactory(
        uploaded_by=user, content=make_pdf(TEXT[name]), doc_type=DocType.CREDIT_CARD_STATEMENT
    )
    provider = PageProvider(pages, missed)
    run_extraction(doc.id, provider)
    doc.refresh_from_db()
    return doc, provider


def api(client, doc):
    return client.get(reverse("document_intake_api:document-result", args=[doc.id])).json()


@pytest.fixture
def staff_client(staff_user):
    client = Client()
    client.force_login(staff_user)
    return client


def test_bmo_response(auth_client, user) -> None:
    doc, provider = extract(user, "bmo")
    assert provider.asked == [1, 2]  # legal pages and the totals-only page are not sent
    assert "inside the section of card 3664" in provider.requests[1].user
    data = api(auth_client, doc)
    assert data["status"] == "ok", data["warnings"]
    assert data["checks"] == {
        "closing_ok": True,
        "debits_ok": True,
        "credits_ok": True,
        "rows_ok": True,
        "section_ok": True,
    }
    assert data["balance"] == {
        "previous": "7296.26",
        "total_credits": "7296.26",
        "total_debits": "6988.41",
        "calculated_closing": "6988.41",
        "printed_closing": "6988.41",
    }
    assert [(r["card_last4"], r["amount"]) for r in data["credits"]] == [
        ("6915", "613.03"),
        ("6915", "6683.23"),
    ]
    first = data["debits"][0]
    assert (first["transaction_number"], first["pre_tax"], first["tax"], first["amount"]) == (
        "626300001",
        "13.26",
        "1.72",
        "14.98",
    )
    assert first["page"] == 1
    assert "6915" not in {r["card_last4"] for r in data["debits"]}


def test_old_response_keys_keep_their_meaning(auth_client, user) -> None:
    doc, _ = extract(user, "bmo")
    data = api(auth_client, doc)
    assert {"header_fields", "lines", "issues", "version"} <= data.keys()
    purchase = next(x for x in data["lines"] if x["debit"] == "14.98")
    assert purchase["amount"] == "-14.98"
    assert purchase["credit"] is None


def test_td_response(auth_client, user) -> None:
    doc, provider = extract(user, "td")
    assert provider.asked == [1, 3]
    data = api(auth_client, doc)
    assert data["status"] == "ok", data["warnings"]
    assert data["checks"]["section_ok"] is None  # TD prints no totals per card
    assert data["balance"]["calculated_closing"] == "19717.42"
    assert (len(data["debits"]), len(data["credits"])) == (33, 7)
    names = {r["description"] for r in data["debits"] + data["credits"]}
    assert not any("BALANCE" in name for name in names)
    assert sum("not a transaction" in w for w in data["warnings"]) == 2
    codes = {i["rule_code"] for i in data["issues"]}
    assert "date_outside_period" not in codes  # Oct 5 purchases posted inside the period
    assert {i["severity"] for i in data["issues"]} == {"warning"}


def test_missing_row_is_found_and_blocks_approval(auth_client, user, staff_user) -> None:
    def drop_one(pages):
        pages[2]["transactions"].pop(4)

    # The second request gets nothing back either.
    doc, provider = extract(user, "bmo", drop_one, missed={2: page([])})
    assert provider.asked == [1, 2, -2]
    data = api(auth_client, doc)
    assert data["status"] == "needs_review"
    assert data["checks"]["rows_ok"] is False
    assert data["checks"]["closing_ok"] is False
    assert data["checks"]["section_ok"] is False
    assert any("26 rows extracted, 27 rows printed" in w for w in data["warnings"])
    with pytest.raises(lifecycle.ApprovalBlocked):
        lifecycle.approve(doc, staff_user)


def test_null_amount_is_flagged(auth_client, user) -> None:
    def blank(pages):
        pages[1]["transactions"][0].update(amount=None, amount_raw=None)

    doc, _ = extract(user, "bmo", blank)
    data = api(auth_client, doc)
    assert [r["card_last4"] for r in data["unclassified"]] == ["6915"]
    assert data["checks"]["rows_ok"] is False
    assert data["balance"]["calculated_closing"] is None


def test_bad_page_is_retried_once(user) -> None:
    class FlakyProvider(PageProvider):
        def complete_json(self, request):
            response = super().complete_json(request)
            if len(self.asked) == 1:
                response.data["rows_returned"] = "many"
                return response
            return type(response)(copy.deepcopy(BMO_PAGES[self.asked[-1]]), "fake-text", 1)

    doc = DocumentFactory(
        uploaded_by=user, content=make_pdf(BMO_TEXT), doc_type=DocType.CREDIT_CARD_STATEMENT
    )
    provider = FlakyProvider(copy.deepcopy(BMO_PAGES))
    assert run_extraction(doc.id, provider) is not None
    assert provider.asked == [1, 1, 2]


def test_page_that_stays_bad_fails_loudly(user) -> None:
    pages = copy.deepcopy(BMO_PAGES)
    pages[2]["rows_returned"] = "many"
    doc = DocumentFactory(
        uploaded_by=user, content=make_pdf(BMO_TEXT), doc_type=DocType.CREDIT_CARD_STATEMENT
    )
    with pytest.raises(MalformedModelOutput):
        run_extraction(doc.id, PageProvider(pages))


def test_scan_cannot_count_rows(auth_client, user) -> None:
    doc = DocumentFactory(
        uploaded_by=user,
        content=make_image(),
        mime_type="image/png",
        original_filename="scan.png",
        doc_type=DocType.CREDIT_CARD_STATEMENT,
    )
    pages = {1: copy.deepcopy(BMO_PAGES[1])}
    run_extraction(doc.id, PageProvider(pages))
    data = api(auth_client, doc)
    assert data["checks"]["rows_ok"] is None
    assert data["status"] == "needs_review"


def test_review_page_tables_and_checks(staff_client, user) -> None:
    doc, _ = extract(user, "bmo")
    html = staff_client.get(
        reverse("document_intake:document-review", kwargs={"pk": doc.pk})
    ).content.decode()
    for text in ("Debits (purchases)", "Credits (payments and cashback)", "6,988.41", "803.98"):
        assert text in html
    assert ">Tax<" in html
    assert 'id="card-banner"' not in html
    assert html.count("checks__item--ok") == 5


def test_review_page_hides_tax_and_shows_banner(staff_client, user) -> None:
    def drop_one(pages):
        pages[3]["transactions"].pop(0)

    doc, _ = extract(user, "td", drop_one, missed={3: page([])})
    html = staff_client.get(
        reverse("document_intake:document-review", kwargs={"pk": doc.pk})
    ).content.decode()
    assert ">Tax<" not in html
    assert 'id="card-banner"' in html
    assert "checks__item--bad" in html


def test_edit_recalculates_and_minus_moves_the_row(staff_client, auth_client, user) -> None:
    doc, _ = extract(user, "bmo")
    line = doc.latest_result().lines.get(debit=Decimal("14.98"))
    target = reverse("document_intake:edit-line", kwargs={"pk": doc.pk, "line_id": line.pk})

    html = staff_client.post(target, {"charge": "-14.98"}, **HTMX).content.decode()
    assert 'id="card-banner"' in html
    line.refresh_from_db()
    assert (line.debit, line.credit) == (None, Decimal("14.98"))
    assert api(auth_client, doc)["checks"]["closing_ok"] is False

    html = staff_client.post(target, {"payment": "-14.98"}, **HTMX).content.decode()
    assert 'id="card-banner"' not in html
    line.refresh_from_db()
    assert (line.debit, line.credit) == (Decimal("14.98"), None)
    assert api(auth_client, doc)["status"] == "ok"


def test_added_row_waits_for_an_amount(staff_client, user) -> None:
    doc, _ = extract(user, "bmo")
    html = staff_client.post(
        reverse("document_intake:add-line", kwargs={"pk": doc.pk}), **HTMX
    ).content.decode()
    assert "Rows without an amount" in html
    assert "Cannot calculate" in html


def test_older_card_results_keep_the_old_table(staff_client, user) -> None:
    doc, _ = extract(user, "bmo")
    result = doc.latest_result()
    result.structured_data = {"transactions": []}
    result.save(update_fields=["structured_data"])
    html = staff_client.get(
        reverse("document_intake:document-review", kwargs={"pk": doc.pk})
    ).content.decode()
    assert "Totals check" in html
    assert "Debits (purchases)" not in html


def test_status_stays_needs_review_until_a_person_approves(user) -> None:
    doc, _ = extract(user, "bmo")
    assert doc.status == DocumentStatus.NEEDS_REVIEW


def _flip(item):
    raw = item["amount_raw"]
    item["amount_raw"] = raw.replace("-", "") if "-" in raw else f"-{raw.replace(' ', '')}"
    item["amount"] = item["amount_raw"].replace("$", "").replace(",", "").replace(" ", "")


@pytest.mark.parametrize(("name", "debits", "credits"), [("bmo", 25, 2), ("td", 33, 7)])
def test_sign_comes_from_the_pdf_even_if_the_model_gets_it_wrong(
    auth_client, user, name, debits, credits
) -> None:
    def flip_all(pages):
        for answer in pages.values():
            for item in answer["transactions"]:
                _flip(item)

    doc, _ = extract(user, name, flip_all)
    data = api(auth_client, doc)
    assert (len(data["debits"]), len(data["credits"])) == (debits, credits)
    assert data["status"] == "ok", data["warnings"]
    assert any("sign" in w for w in data["warnings"])


def test_last_row_under_a_new_card_is_asked_for_again(auth_client, user) -> None:
    """Seen with qwen2.5:7b: page 2 ended at card 3664's totals and left out card 5124's row."""
    touchbistro = copy.deepcopy(BMO_PAGES[2]["transactions"][-1])

    def drop_last(pages):
        pages[2]["transactions"].pop()

    doc, provider = extract(user, "bmo", drop_last, missed={2: page([touchbistro])})
    assert provider.asked == [1, 2, -2]
    missed_request = provider.requests[-1].user
    assert "12/28 12/29 SHOP 25 TORONTO ON" in missed_request
    assert "SHOP 24" not in missed_request
    data = api(auth_client, doc)
    assert data["status"] == "ok", data["warnings"]
    assert data["checks"]["section_ok"] is True
    assert data["debits"][-1]["card_last4"] == "5124"
    assert [x["line_no"] for x in data["lines"]] == list(range(1, 28))


def test_balance_the_pdf_does_not_print_is_flagged(auth_client, user) -> None:
    """Seen with qwen2.5:7b: New Account Balance read as 7,942.00."""

    def misread(pages):
        pages[1]["summary"]["new_balance"] = "$ 7,942.00"

    doc, _ = extract(user, "bmo", misread)
    data = api(auth_client, doc)
    assert data["status"] == "needs_review"
    assert data["checks"]["closing_ok"] is False
    assert any("7,942.00 is not printed anywhere in the PDF" in w for w in data["warnings"])
    issue = next(i for i in data["issues"] if i["rule_code"] == "card_value_not_in_text")
    assert issue["field_key"] == "closing_balance"
