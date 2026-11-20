"""HTMX pages: full pages, partials, edits, actions, files and safety checks."""

import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.staticfiles import finders
from django.test import Client, override_settings
from django.urls import reverse

from document_intake.models import AuditAction, AuditLog, DocType, Document, DocumentStatus
from document_intake.services.extraction import run_extraction
from document_intake.tests.factories import DocumentFactory
from document_intake.tests.fakes import BANK_STATEMENT, RECEIPT, FakeProvider
from document_intake.tests.unit.test_sources import statement_pdf
from document_intake.ui import totals
from document_intake.ui.forms import LineForm

pytestmark = pytest.mark.django_db
S = DocumentStatus
LIST = reverse("document_intake:document-list")
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def ui_client(client, user):
    """The client who uploads (may only look at the review page)."""
    client.force_login(user)
    return client


@pytest.fixture
def staff_ui_client(staff_user):
    """The firm's reviewer (edits, approves, rejects, exports)."""
    reviewer = Client()
    reviewer.force_login(staff_user)
    return reviewer


def url(name, doc, **kwargs):
    return reverse(f"document_intake:{name}", kwargs={"pk": doc.pk, **kwargs})


def extracted(user, data=BANK_STATEMENT, doc_type=DocType.BANK_STATEMENT):
    doc = DocumentFactory(uploaded_by=user, content=statement_pdf(), doc_type=doc_type)
    run_extraction(doc.id, FakeProvider(data))
    doc.refresh_from_db()
    return doc


def field(doc, key):
    return doc.latest_result().fields.get(key=key)


def line(doc, line_no):
    return doc.latest_result().lines.get(line_no=line_no)


# ------------------------------------------------------------------- delete


def test_delete_one_document(ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, status=S.NEEDS_REVIEW)
    response = ui_client.post(url("delete-document", doc), **HTMX)
    assert response.status_code == 200
    assert response["HX-Trigger"] == "documents-changed"
    assert response["HX-Retarget"] == "#messages"
    assert "Deleted" in response.content.decode()
    assert not Document.objects.filter(pk=doc.pk).exists()
    gone = Document.all_objects.get(pk=doc.pk)
    assert gone.deleted_by == user
    assert AuditLog.objects.filter(document=doc, action=AuditAction.DELETE).exists()
    assert doc.original_filename not in ui_client.get(LIST, **HTMX).content.decode()


@pytest.mark.parametrize("status", [S.UPLOADED, S.PROCESSING])
def test_stuck_document_can_be_deleted_and_worker_skips_it(ui_client, user, status) -> None:
    doc = DocumentFactory(uploaded_by=user, status=status)
    html = ui_client.get(LIST).content.decode()
    assert url("delete-document", doc) in html
    ui_client.post(url("delete-document", doc), **HTMX)
    assert not Document.objects.filter(pk=doc.pk).exists()
    # The queued task arrives later: nothing happens, no error.
    assert run_extraction(doc.id, FakeProvider(BANK_STATEMENT)) is None
    assert Document.all_objects.get(pk=doc.pk).results.count() == 0


def test_delete_from_review_page_goes_back_to_list(ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, status=S.FAILED)
    response = ui_client.post(url("delete-document", doc), {"next": "list"}, **HTMX)
    assert response.status_code == 204
    assert response["HX-Redirect"] == LIST


@pytest.mark.parametrize("status", [S.APPROVED, S.EXPORTED])
def test_delete_blocked_once_booked(ui_client, user, status) -> None:
    doc = DocumentFactory(uploaded_by=user, status=status)
    response = ui_client.post(url("delete-document", doc), **HTMX)
    assert "cannot be deleted" in response.content.decode()
    assert "HX-Trigger" not in response
    assert Document.objects.filter(pk=doc.pk).exists()


def test_delete_other_users_document_is_404(ui_client, other_user) -> None:
    doc = DocumentFactory(uploaded_by=other_user, status=S.NEEDS_REVIEW)
    assert ui_client.post(url("delete-document", doc), **HTMX).status_code == 404
    assert Document.objects.filter(pk=doc.pk).exists()


def test_delete_needs_post(ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, status=S.NEEDS_REVIEW)
    assert ui_client.get(url("delete-document", doc)).status_code == 405


def test_bulk_delete(ui_client, user, other_user) -> None:
    mine = [DocumentFactory(uploaded_by=user, status=S.NEEDS_REVIEW) for _ in range(2)]
    approved = DocumentFactory(uploaded_by=user, status=S.APPROVED)
    theirs = DocumentFactory(uploaded_by=other_user, status=S.NEEDS_REVIEW)
    ids = [str(d.pk) for d in [*mine, approved, theirs]] + ["not-a-uuid"]
    response = ui_client.post(reverse("document_intake:delete-documents"), {"ids": ids}, **HTMX)
    text = response.content.decode()
    assert "Deleted 2 documents" in text
    assert "cannot be deleted" in text
    assert "notice--warn" in text
    assert response["HX-Trigger"] == "documents-changed"
    assert not Document.objects.filter(pk__in=[d.pk for d in mine]).exists()
    assert Document.objects.filter(pk__in=[approved.pk, theirs.pk]).count() == 2


def test_bulk_delete_without_selection(ui_client) -> None:
    response = ui_client.post(reverse("document_intake:delete-documents"), {}, **HTMX)
    assert "Select at least one" in response.content.decode()


def test_list_shows_delete_controls_only_for_deletable_rows(ui_client, user) -> None:
    deletable = DocumentFactory(uploaded_by=user, status=S.NEEDS_REVIEW)
    booked = DocumentFactory(uploaded_by=user, status=S.APPROVED)
    html = ui_client.get(LIST).content.decode()
    assert url("delete-document", deletable) in html
    assert url("delete-document", booked) not in html
    assert 'id="bulk-delete"' in html
    assert 'id="confirm-dialog"' in html


# ------------------------------------------------------------------- login


def test_pages_require_login(client) -> None:
    response = client.get(LIST)
    assert response.status_code == 302
    assert response["Location"] == f"/accounts/login/?next={LIST}"


def test_login_flow(client, user) -> None:
    assert client.get("/accounts/login/").status_code == 200
    bad = client.post("/accounts/login/", {"username": user.username, "password": "wrong"})
    assert b"not correct" in bad.content
    good = client.post(
        "/accounts/login/", {"username": user.username, "password": "pass-for-tests"}
    )
    assert good.status_code == 302
    assert good["Location"] == LIST


def test_logout_requires_post(ui_client) -> None:
    assert ui_client.get("/accounts/logout/").status_code == 405
    assert ui_client.post("/accounts/logout/").status_code == 302


def test_post_without_csrf_token_is_refused(user, staff_user) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff_user)  # may edit, so a 403 here can only be the CSRF check
    doc = extracted(user)
    response = client.post(url("add-line", doc), **HTMX)
    assert response.status_code == 403
    assert b"CSRF" in response.content


# -------------------------------------------------------------------- list


def test_list_page_shell(ui_client) -> None:
    response = ui_client.get(LIST)
    html = response.content.decode()
    assert response.status_code == 200
    assert "document_intake/vendor/htmx.min.js" in html
    assert 'hx-headers=\'{"X-CSRFToken": "' in html
    assert '"allowEval": false' in html
    assert 'hx-post="/intake/upload/"' in html
    assert "JPEG, JPG, PDF, PNG" in html
    assert 'action="/accounts/logout/"' in html
    assert "script-src 'self'" in response["Content-Security-Policy"]
    assert "unsafe" not in response["Content-Security-Policy"]


def test_list_htmx_request_gets_only_the_table(ui_client, user) -> None:
    DocumentFactory(uploaded_by=user, original_filename="sept.pdf")
    DocumentFactory(uploaded_by=user, original_filename="oct.pdf")
    response = ui_client.get(LIST, {"search": "sept"}, **HTMX)
    html = response.content.decode()
    assert "<html" not in html
    assert 'id="documents"' in html
    assert "sept.pdf" in html
    assert "oct.pdf" not in html


def test_list_hides_other_users_documents(ui_client, other_user) -> None:
    DocumentFactory(uploaded_by=other_user, original_filename="secret.pdf")
    assert b"secret.pdf" not in ui_client.get(LIST).content


def test_list_polls_only_while_processing(ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, status=S.PROCESSING)
    assert b"every 4s" in ui_client.get(LIST, **HTMX).content
    Document.objects.filter(pk=doc.pk).update(status=S.NEEDS_REVIEW)
    assert b"every 4s" not in ui_client.get(LIST, **HTMX).content


def test_list_pages_and_bad_filters(ui_client, user) -> None:
    for n in range(27):
        DocumentFactory(uploaded_by=user, original_filename=f"doc-{n:02}.pdf")
    first = ui_client.get(LIST, {"ordering": "original_filename"}, **HTMX).content.decode()
    assert "of 27" in first
    assert "page=2" in first
    second = ui_client.get(LIST, {"ordering": "original_filename", "page": 2}, **HTMX)
    assert b"doc-26.pdf" in second.content
    # An invalid filter value is ignored instead of crashing the page.
    assert ui_client.get(LIST, {"created_from": "not-a-date"}).status_code == 200


def test_upload_creates_documents(ui_client, user, pdf_bytes, make_upload) -> None:
    files = [make_upload("a.pdf", pdf_bytes), make_upload("evil.pdf", b"MZ\x90\x00 not a pdf")]
    with override_settings(CELERY_TASK_ALWAYS_EAGER=False):
        response = ui_client.post(
            reverse("document_intake:upload"),
            {"files": files, "doc_type": DocType.RECEIPT},
            **HTMX,
        )
    html = response.content.decode()
    assert response["HX-Trigger"] == "documents-changed"
    assert "a.pdf" in html
    assert "Uploaded" in html
    assert "queue__message--error" in html  # the fake PDF is rejected with a message
    assert Document.objects.get(uploaded_by=user).doc_type == DocType.RECEIPT


def test_upload_without_files(ui_client) -> None:
    response = ui_client.post(reverse("document_intake:upload"), {}, **HTMX)
    assert b"Choose at least one file." in response.content


# ------------------------------------------------------------------ review


def test_review_page_shows_extracted_data(staff_ui_client, user) -> None:
    doc = extracted(user)
    html = staff_ui_client.get(url("document-review", doc)).content.decode()
    assert 'id="review-body"' in html
    assert "Transactions" in html
    assert "Totals check" in html
    assert 'id="field-closing_balance"' in html
    assert "hx-post=" in html
    assert 'id="messages"' in html


def test_review_page_escapes_filenames(ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, original_filename="<b>td</b>.pdf")
    response = ui_client.get(url("document-review", doc))
    assert b"&lt;b&gt;td&lt;/b&gt;.pdf" in response.content


def test_review_page_of_other_user_is_404(ui_client, other_user) -> None:
    doc = DocumentFactory(uploaded_by=other_user)
    assert ui_client.get(url("document-review", doc)).status_code == 404


def test_review_status_polling(ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, status=S.PROCESSING)
    assert ui_client.get(url("review-status", doc), **HTMX).status_code == 204
    Document.objects.filter(pk=doc.pk).update(status=S.NEEDS_REVIEW)
    response = ui_client.get(url("review-status", doc), **HTMX)
    assert response["HX-Refresh"] == "true"


def test_edit_field_returns_fresh_panels(staff_ui_client, user) -> None:
    doc = extracted(user)
    target = field(doc, "closing_balance")
    response = staff_ui_client.post(
        url("edit-field", doc, field_id=target.pk), {"value": "4300.00"}, **HTMX
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert html.lstrip().startswith("<div")
    assert 'hx-swap-oob="true"' in html  # clears an old error message
    assert "Off by" in html  # the totals no longer match
    target.refresh_from_db()
    assert target.value == "4300.00"


def test_edit_line_accepts_people_style_money(staff_ui_client, user) -> None:
    doc = extracted(user)
    row = line(doc, 1)
    staff_ui_client.post(url("edit-line", doc, line_id=row.pk), {"debit": "$1,234.50"}, **HTMX)
    row.refresh_from_db()
    assert row.debit == Decimal("1234.50")
    assert row.description  # columns that were not sent are untouched


def test_edit_line_invalid_value_shows_message(staff_ui_client, user) -> None:
    doc = extracted(user)
    row = line(doc, 1)
    response = staff_ui_client.post(url("edit-line", doc, line_id=row.pk), {"debit": "abc"}, **HTMX)
    assert response["HX-Retarget"] == "#messages"
    assert b"Not saved" in response.content


def test_add_delete_line_and_resolve_issue(staff_ui_client, user) -> None:
    doc = extracted(user, {**BANK_STATEMENT, "closing_balance": "4300.00"})
    count = doc.latest_result().lines.count()
    staff_ui_client.post(url("add-line", doc), **HTMX)
    assert doc.latest_result().lines.count() == count + 1
    staff_ui_client.post(url("delete-line", doc, line_id=line(doc, 1).pk), **HTMX)
    assert doc.latest_result().lines.count() == count

    issue = doc.latest_result().issues.filter(severity="error").first()
    staff_ui_client.post(url("resolve-issue", doc, issue_id=issue.pk), {"resolved": "1"}, **HTMX)
    issue.refresh_from_db()
    assert issue.resolved


def test_unknown_line_is_404(staff_ui_client, user) -> None:
    doc = extracted(user)
    missing = "00000000-0000-0000-0000-000000000001"
    assert staff_ui_client.post(url("edit-line", doc, line_id=missing), **HTMX).status_code == 404


def test_approve_blocked_then_acknowledged(staff_ui_client, user) -> None:
    doc = extracted(user, {**BANK_STATEMENT, "closing_balance": "4300.00"})
    blocked = staff_ui_client.post(url("approve", doc), **HTMX)
    assert blocked["HX-Retarget"] == "#messages"
    assert b"unresolved error" in blocked.content

    ok = staff_ui_client.post(
        url("approve", doc), {"acknowledge_errors": "on"}, HTTP_HX_PROMPT="bank typo", **HTMX
    )
    assert ok["HX-Refresh"] == "true"
    doc.refresh_from_db()
    assert doc.status == S.APPROVED
    entry = AuditLog.objects.get(document=doc, action=AuditAction.APPROVE)
    assert entry.metadata["note"] == "bank typo"


def test_reject_and_clarify_need_text(staff_ui_client, user) -> None:
    doc = extracted(user)
    assert b"Write a reason" in staff_ui_client.post(url("reject", doc), **HTMX).content
    assert b"Write the question" in staff_ui_client.post(url("clarify", doc), **HTMX).content

    staff_ui_client.post(url("clarify", doc), HTTP_HX_PROMPT="Which card?", **HTMX)
    doc.refresh_from_db()
    assert doc.status == S.NEEDS_CLARIFICATION
    staff_ui_client.post(url("resume-review", doc), **HTMX)
    staff_ui_client.post(url("reject", doc), HTTP_HX_PROMPT="Wrong document", **HTMX)
    doc.refresh_from_db()
    assert doc.status == S.REJECTED


def test_mark_duplicate_and_reprocess(
    staff_ui_client, user, django_capture_on_commit_callbacks
) -> None:
    doc = extracted(user)
    staff_ui_client.post(url("mark-duplicate", doc), **HTMX)
    doc.refresh_from_db()
    assert doc.status == S.DUPLICATE
    with override_settings(CELERY_TASK_ALWAYS_EAGER=False):
        response = staff_ui_client.post(url("reprocess", doc), **HTMX)
    assert response["HX-Refresh"] == "true"
    doc.refresh_from_db()
    assert doc.status == S.UPLOADED


def test_wrong_state_action_shows_message(staff_ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user, status=S.APPROVED)
    response = staff_ui_client.post(url("resume-review", doc), **HTMX)
    assert response["HX-Retarget"] == "#messages"


def test_client_sees_review_page_without_controls(ui_client, user) -> None:
    doc = extracted(user)
    html = ui_client.get(url("document-review", doc)).content.decode()
    assert "Your accounting firm reviews and approves it" in html
    assert 'id="field-closing_balance"' in html  # the data is shown...
    # ...but nothing can be changed. Deleting their own upload is the only action.
    assert html.count("hx-post=") == 1
    assert url("delete-document", doc) in html
    for name in ("Approve", "Reject", "Export CSV", "Add row", "Acknowledge"):
        assert f">{name}<" not in html


def test_client_cannot_review(ui_client, user) -> None:
    doc = extracted(user)
    edit = url("edit-field", doc, field_id=field(doc, "currency").pk)
    assert ui_client.post(edit, {"value": "USD"}, **HTMX).status_code == 403
    row = url("edit-line", doc, line_id=line(doc, 1).pk)
    assert ui_client.post(row, {"debit": "abc"}, **HTMX).status_code == 403
    assert ui_client.post(url("add-line", doc), **HTMX).status_code == 403
    assert ui_client.post(url("approve", doc), **HTMX).status_code == 403
    assert ui_client.post(url("reject", doc), HTTP_HX_PROMPT="x", **HTMX).status_code == 403
    assert ui_client.get(url("export-csv", doc)).status_code == 403
    doc.refresh_from_db()
    assert doc.status == S.NEEDS_REVIEW
    assert field(doc, "currency").value != "USD"


def test_client_notice_while_firm_asks_a_question(ui_client, staff_ui_client, user) -> None:
    doc = extracted(user)
    staff_ui_client.post(url("clarify", doc), HTTP_HX_PROMPT="Which card?", **HTMX)
    html = ui_client.get(url("document-review", doc)).content.decode()
    assert "Your accountant has a question" in html
    assert "You can still edit" not in html


def test_review_permission_hook_can_let_uploader_review(ui_client, user) -> None:
    doc = extracted(user)
    owner_or_staff = ("document_intake.api.permissions.IsOwnerOrStaff",)
    with override_settings(DOCUMENT_INTAKE={"REVIEW_PERMISSION_CLASSES": owner_or_staff}):
        assert ">Approve<" in ui_client.get(url("document-review", doc)).content.decode()
        ui_client.post(url("approve", doc), **HTMX)
    doc.refresh_from_db()
    assert doc.status == S.APPROVED


def test_edits_blocked_on_other_users_document(ui_client, other_user) -> None:
    doc = extracted(other_user)
    assert ui_client.post(url("add-line", doc), **HTMX).status_code == 404


# ---------------------------------------------------- viewer, files, export


def test_viewer_and_page_image(ui_client, user) -> None:
    doc = extracted(user)
    html = ui_client.get(url("viewer", doc), {"page": "99"}, **HTMX).content.decode()
    assert "Page 1 of 1" in html  # out-of-range pages are clamped
    image = ui_client.get(url("page-image", doc, number=1))
    assert image["Content-Type"] == "image/png"
    assert image["Cache-Control"] == "private, no-store"
    assert ui_client.get(url("page-image", doc, number=5)).status_code == 404


def test_original_file_is_audited(ui_client, user) -> None:
    doc = DocumentFactory(uploaded_by=user)
    response = ui_client.get(url("file", doc), {"download": "1"})
    assert b"".join(response.streaming_content) == b"%PDF-1.4 test document"
    assert response["Content-Disposition"].startswith("attachment")
    assert response["X-Content-Type-Options"] == "nosniff"
    entry = AuditLog.objects.get(document=doc, action=AuditAction.DOWNLOAD)
    assert entry.metadata == {"disposition": "attachment"}


def test_export_only_after_approval(staff_ui_client, user) -> None:
    doc = extracted(user, RECEIPT, DocType.RECEIPT)
    assert staff_ui_client.get(url("export-csv", doc)).status_code == 409
    staff_ui_client.post(url("approve", doc), {"acknowledge_errors": "on"}, **HTMX)
    response = staff_ui_client.get(url("export-csv", doc))
    assert response.status_code == 200
    assert response.content.decode().startswith("Date,Vendor,Account")
    doc.refresh_from_db()
    assert doc.status == S.EXPORTED


def test_activity_partial(staff_ui_client, user) -> None:
    doc = extracted(user)
    staff_ui_client.post(
        url("edit-field", doc, field_id=field(doc, "currency").pk), {"value": "USD"}
    )
    html = staff_ui_client.get(url("activity", doc), **HTMX).content.decode()
    assert "currency:" in html
    assert "USD" in html
    assert "Version 1" in html


# ------------------------------------------------------------- pure helpers


def test_statement_totals() -> None:
    lines = [SimpleNamespace(debit=Decimal("100"), credit=None)]
    lines.append(SimpleNamespace(debit=None, credit=Decimal("40")))
    rows = totals.statement_rows(
        {"opening_balance": "1,000.00", "closing_balance": "940.00"}, lines, False
    )
    assert rows[3]["value"] == Decimal("940.00")
    assert rows[-1]["check"]["ok"] is True
    card = totals.statement_rows({"opening_balance": "0", "closing_balance": "x"}, lines, True)
    assert card[3]["value"] == Decimal("60")
    assert card[-1]["check"]["ok"] is None  # unreadable closing balance cannot be checked


def test_receipt_totals() -> None:
    lines = [SimpleNamespace(amount=Decimal("10.00")), SimpleNamespace(amount=None)]
    rows = totals.receipt_rows({"subtotal": "10.00", "hst": "1.30", "total": "($11.40)"}, lines)
    by_label = {row["label"]: row for row in rows}
    assert by_label["Lines vs subtotal"]["check"]["ok"] is True
    assert by_label["Calculated total"]["value"] == Decimal("11.30")
    assert by_label["Difference"]["check"]["ok"] is False
    assert totals.receipt_rows({}, [])[0]["value"] is None


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("$1,234.56", Decimal("1234.56")), ("(12.00)", Decimal("-12.00")), ("", None)],
)
def test_money_input(typed, expected) -> None:
    form = LineForm({"amount": typed})
    assert form.is_valid()
    assert form.cleaned_data["amount"] == expected


def test_line_form_rejects_negative_debit() -> None:
    form = LineForm({"debit": "-5"})
    assert not form.is_valid()
    assert "debit" in form.error_text()


# ------------------------------------------------------------ static files


@pytest.mark.parametrize(
    "path",
    [
        "document_intake/css/tokens.css",
        "document_intake/css/base.css",
        "document_intake/css/layout.css",
        "document_intake/css/components.css",
        "document_intake/css/review.css",
        "document_intake/js/upload.js",
        "document_intake/js/ui.js",
        "document_intake/js/list.js",
        "document_intake/vendor/htmx.min.js",
    ],
)
def test_static_assets_exist(path) -> None:
    assert finders.find(path), path


def test_no_external_requests_in_assets() -> None:
    """No CDNs, web fonts or remote URLs: everything is served by us."""
    for name in ("tokens", "base", "layout", "components", "review"):
        content = pathlib.Path(finders.find(f"document_intake/css/{name}.css")).read_text()
        for needle in ("http://", "https://", "@import", "url("):
            assert needle not in content, (name, needle)


@pytest.mark.parametrize("name", ["upload", "ui", "list"])
def test_our_js_never_writes_html(name) -> None:
    js = pathlib.Path(finders.find(f"document_intake/js/{name}.js")).read_text()
    for needle in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert needle not in js


@pytest.mark.parametrize(
    ("action", "metadata", "expected"),
    [
        (
            "upload",
            {"mime_type": "image/png", "duplicate_of": "x"},
            "Uploaded (image/png), duplicate",
        ),
        ("extract", {"outcome": "failed"}, "Extraction failed"),
        (
            "status_change",
            {"from": "needs_review", "to": "rejected", "reason": "r"},
            "Needs review →",
        ),
        (
            "status_change",
            {"from": "needs_review", "to": "needs_clarification", "question": "q"},
            "“q”",
        ),
        ("edit", {"target": "issue", "rule_code": "R1", "resolved": True}, "R1 acknowledged"),
        ("edit", {"target": "line", "operation": "add", "line_no": 3}, "Added line 3"),
        ("edit", {"target": "line", "operation": "delete", "line_no": 3}, "Removed line 3"),
        (
            "edit",
            {"target": "line", "line_no": 2, "before": {"debit": "1"}, "after": {"debit": "2"}},
            "Line 2: debit “1” → “2”",
        ),
        ("approve", {"acknowledged": ["R1"], "note": "ok"}, "Approved · acknowledged R1 · ok"),
        ("reject", {"reason": "blurry"}, "Rejected · blurry"),
        ("download", {"disposition": "inline"}, "Viewed the original"),
        ("download", {"disposition": "attachment"}, "Downloaded the original"),
        ("export", {"rows": 4, "version": 1}, "Exported CSV (4 rows, version 1)"),
        ("reprocess", {}, "Requested a new extraction"),
        ("delete", {}, "Deleted the document"),
    ],
)
def test_activity_sentences(action, metadata, expected) -> None:
    from document_intake.ui.activity import actor_name, describe

    entry = AuditLog(action=action, metadata=metadata)
    assert expected in describe(entry)
    assert actor_name(entry) == "System"
