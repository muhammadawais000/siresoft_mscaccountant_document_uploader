"""UI views for the HTMX pages.

How it works, in short:

* A normal request (typing the URL, a link) gets the **full page**.
* A request made by HTMX (header ``HX-Request``) gets only a **piece of HTML** (a
  "partial" from ``templates/document_intake/partials/``), and HTMX swaps that piece
  into the page. No JSON and no JavaScript rendering.
* Business rules stay in ``document_intake.services``; these views only read the form,
  call one service function and render a template.

The JSON API in ``document_intake.api`` is still there for other clients (for example a
React portal). These pages do not use it.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
)
from django.http.response import HttpResponseBase
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_POST

from document_intake.api.permissions import (
    configured_permission_classes,
    configured_review_permission_classes,
)
from document_intake.api.throttles import UploadRateThrottle
from document_intake.conf import intake_settings
from document_intake.exceptions import IntakeError
from document_intake.extraction import sources
from document_intake.extraction.schemas import AMOUNT_KEYS, DATE_KEYS, REVIEWER_KEYS, family
from document_intake.models import (
    AuditAction,
    DocType,
    Document,
    DocumentStatus,
    ExtractedLine,
    ExtractionMethod,
    ExtractionResult,
)
from document_intake.services import access, audit, export, lifecycle, query, review, upload

from . import activity, totals
from .forms import ORDERINGS, FieldForm, FilterForm, LineForm

PAGE_SIZE = 25
POLL_SECONDS = 4
IN_PROGRESS = {DocumentStatus.UPLOADED, DocumentStatus.PROCESSING}
EDITABLE = {DocumentStatus.NEEDS_REVIEW, DocumentStatus.NEEDS_CLARIFICATION}
CAN_REJECT = {*EDITABLE, DocumentStatus.DUPLICATE, DocumentStatus.FAILED}

STATEMENT_COLUMNS = [
    {"key": "date", "label": "Date", "kind": "date"},
    {"key": "description", "label": "Description", "kind": "text"},
    {"key": "debit", "label": "Debit", "kind": "money"},
    {"key": "credit", "label": "Credit", "kind": "money"},
    {"key": "balance", "label": "Balance", "kind": "money"},
]
RECEIPT_COLUMNS = [
    {"key": "description", "label": "Description", "kind": "text"},
    {"key": "amount", "label": "Amount", "kind": "money"},
    {"key": "tax", "label": "Tax", "kind": "money"},
]

# Pages load only our own files. htmx is configured (base.html) not to inject styles or
# evaluate code, so no 'unsafe-inline' or 'unsafe-eval' is needed.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


# --------------------------------------------------------------------------- helpers


def intake_view(view: Callable[..., HttpResponseBase]) -> Callable[..., HttpResponseBase]:
    """Login required + the Content-Security-Policy header on every response."""

    @login_required
    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        response = view(request, *args, **kwargs)
        response.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    return wrapper


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _logout_url() -> str | None:
    try:
        return reverse("logout")
    except NoReverseMatch:
        return None


def _page_context(**extra: Any) -> dict[str, Any]:
    return {"nav_active": "documents", "logout_url": _logout_url(), **extra}


def get_document(request: HttpRequest, pk: Any, *, review_action: bool = False) -> Document:
    """The document if this user may see it (else 404), and act on it (else 403).

    Uses the same permission hooks as the API (``DOCUMENT_INTAKE`` settings), so a portal
    that restricts approving to staff restricts it here too.
    """
    document = access.visible_documents(request.user).filter(pk=pk).first()
    if document is None:
        raise Http404("Document not found.")
    if not is_allowed(request, document, review_action=review_action):
        raise PermissionDenied
    return document


def is_allowed(request: HttpRequest, document: Document, *, review_action: bool = False) -> bool:
    """True if every configured permission class lets this user act on the document.

    With ``review_action=True`` the REVIEW_PERMISSION_CLASSES are checked too (edit,
    approve, reject, export). The review page also uses this to hide those buttons.
    """
    classes = configured_permission_classes()
    if review_action:
        classes += configured_review_permission_classes()
    req: Any = request  # DRF permission classes accept a plain Django request too
    for permission_class in classes:
        permission = permission_class()
        if not permission.has_permission(req, None):  # type: ignore[arg-type]
            return False
        if not permission.has_object_permission(req, None, document):  # type: ignore[arg-type]
            return False
    return True


def show_message(request: HttpRequest, text: str, tone: str = "err") -> HttpResponse:
    """Put a message in the page's message area instead of the usual target."""
    response = render(
        request, "document_intake/partials/message.html", {"text": text, "tone": tone}
    )
    response["HX-Retarget"] = "#messages"
    response["HX-Reswap"] = "innerHTML"
    return response


def show_and_refresh_list(request: HttpRequest, text: str, tone: str) -> HttpResponse:
    """A message for #messages, and tell the documents table to reload itself."""
    response = show_message(request, text, tone)
    response["HX-Trigger"] = "documents-changed"
    return response


def refresh_page() -> HttpResponse:
    """Tell HTMX to reload the whole page (used after a status change)."""
    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


# ------------------------------------------------------------------- documents list


@intake_view
@require_GET
def document_list(request: HttpRequest) -> HttpResponse:
    """The list page. HTMX asks for it again to filter, sort, page and poll."""
    form = FilterForm(request.GET)
    documents = query.filter_documents(access.visible_documents(request.user), form.filters())
    page = Paginator(query.with_issue_counts(documents), PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    type_labels = dict(DocType.choices)
    for doc in page:
        doc.type_label = type_labels.get(doc.effective_doc_type or doc.doc_type, "")  # type: ignore[attr-defined]
        doc.can_delete = lifecycle.can_delete(doc.status)  # type: ignore[attr-defined]
    context = {
        "page": page,
        "form": form,
        "ordering": form.cleaned_data.get("ordering") if form.is_valid() else "",
        "filtered": bool(form.filters().keys() - {"ordering"}),
        # Keep asking for this table every few seconds while something is still processing.
        "poll": any(doc.status in IN_PROGRESS for doc in page),
        "poll_seconds": POLL_SECONDS,
    }
    if is_htmx(request):
        return render(request, "document_intake/partials/documents_table.html", context)

    allowed: dict[str, tuple[str, ...]] = intake_settings.ALLOWED_MIME_TYPES
    extensions = sorted({ext for exts in allowed.values() for ext in exts})
    context.update(
        _page_context(
            statuses=DocumentStatus.choices,
            doc_types=DocType.choices,
            orderings=ORDERINGS,
            accept=",".join([*extensions, *allowed]),
            allowed_extensions_text=", ".join(e.lstrip(".").upper() for e in extensions),
            max_upload_mb=int(intake_settings.MAX_UPLOAD_BYTES) // (1024 * 1024),
        )
    )
    return render(request, "document_intake/document_list.html", context)


@intake_view
@require_POST
def upload_documents(request: HttpRequest) -> HttpResponse:
    """Save each chosen file through the upload service and report one line per file."""
    files = request.FILES.getlist("files")
    doc_type = request.POST.get("doc_type", DocType.AUTO)
    if doc_type not in DocType.values:
        doc_type = DocType.AUTO
    throttle = UploadRateThrottle()
    results = []
    for file in files:
        if not throttle.allow_request(request, None):  # type: ignore[arg-type]
            results.append({"name": file.name, "ok": False, "text": "Too many uploads. Try later."})
            continue
        try:
            document = upload.create_document(
                upload=file, uploaded_by=request.user, doc_type=doc_type
            )
        except IntakeError as exc:
            results.append({"name": file.name, "ok": False, "text": str(exc)})
            continue
        text = "Same file as an earlier upload" if document.duplicate_of_id else "Uploaded"
        results.append({"name": document.original_filename, "ok": True, "text": text})
    if not files:
        results.append({"name": "", "ok": False, "text": "Choose at least one file."})

    response = render(request, "document_intake/partials/upload_results.html", {"results": results})
    # The documents table listens for this event and reloads itself.
    response["HX-Trigger"] = "documents-changed"
    return response


MAX_BULK_DELETE = 100


@intake_view
@require_POST
def delete_document(request: HttpRequest, pk: Any) -> HttpResponse:
    """Delete one document. From the review page, go back to the list afterwards."""
    document = get_document(request, pk)
    try:
        lifecycle.delete_document(document, request.user)
    except IntakeError as exc:
        return show_message(request, str(exc))
    if request.POST.get("next") == "list":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = reverse("document_intake:document-list")
        return response
    return show_and_refresh_list(request, f"Deleted “{document.original_filename}”.", "ok")


def _uuids(values: list[str]) -> list[uuid.UUID]:
    ids = []
    for value in values[:MAX_BULK_DELETE]:
        try:
            ids.append(uuid.UUID(value))
        except ValueError:
            continue
    return ids


@intake_view
@require_POST
def delete_documents(request: HttpRequest) -> HttpResponse:
    """Delete the ticked documents. Each one is checked and deleted on its own."""
    ids = _uuids(request.POST.getlist("ids"))
    if not ids:
        return show_message(request, "Select at least one document to delete.", "warn")
    documents = access.visible_documents(request.user).filter(pk__in=ids)
    deleted, problems = 0, []
    for document in documents:
        if not is_allowed(request, document):
            problems.append(f"“{document.original_filename}”: not allowed.")
            continue
        try:
            lifecycle.delete_document(document, request.user)
        except IntakeError as exc:
            problems.append(str(exc))
            continue
        deleted += 1
    text = f"Deleted {deleted} document{'s' if deleted != 1 else ''}."
    if problems:
        text += " Not deleted: " + " ".join(problems)
    tone = "ok" if not problems else ("warn" if deleted else "err")
    return show_and_refresh_list(request, text, tone)


# -------------------------------------------------------------------- review page


def _latest_result(document: Document) -> ExtractionResult | None:
    return (
        ExtractionResult.objects.filter(document=document)
        .prefetch_related(
            "fields",
            "issues",
            Prefetch("lines", queryset=ExtractedLine.objects.order_by("line_no")),
        )
        .order_by("-version")
        .first()
    )


def review_context(document: Document, *, can_review: bool) -> dict[str, Any]:
    """Everything the review panels need, worked out on the server.

    ``can_review`` is False for users who may only look (e.g. a client in a portal that
    keeps approving to staff): then every edit control is hidden.
    """
    result = _latest_result(document)
    context: dict[str, Any] = {
        "document": document,
        "result": result,
        "can_review": can_review,
        "editable": can_review and document.status in EDITABLE,
        "in_progress": document.status in IN_PROGRESS,
        "poll_seconds": POLL_SECONDS,
        "doc_type_label": dict(DocType.choices).get(
            document.effective_doc_type or document.doc_type, ""
        ),
    }
    if result is None:
        return context

    fields = sorted(result.fields.all(), key=lambda f: f.position)
    lines = list(result.lines.all())
    values = {f.key: f.value for f in fields}
    statement = family(document.effective_doc_type) == "statement"
    issues = list(result.issues.all())
    open_issues = [i for i in issues if not i.resolved]
    open_errors = [i for i in open_issues if i.severity == "error"]
    labels = {f.key: f.label for f in fields}

    for field in fields:
        field.kind = (  # type: ignore[attr-defined]
            "money" if field.key in AMOUNT_KEYS else "date" if field.key in DATE_KEYS else "text"
        )
        field.reviewer_enters = field.key in REVIEWER_KEYS  # type: ignore[attr-defined]
    for issue in issues:
        issue.where = (  # type: ignore[attr-defined]
            f"Line {issue.line_no}" if issue.line_no else labels.get(issue.field_key, "Document")
        )
        issue.anchor = (  # type: ignore[attr-defined]
            f"line-{issue.line_no}" if issue.line_no else f"field-{issue.field_key}"
        )

    columns = STATEMENT_COLUMNS if statement else RECEIPT_COLUMNS
    for line in lines:
        line.cells = [  # type: ignore[attr-defined]
            {**col, "value": getattr(line, col["key"])} for col in columns
        ]

    if statement:
        credit_card = document.effective_doc_type == DocType.CREDIT_CARD_STATEMENT
        totals_rows = totals.statement_rows(values, lines, credit_card)
    else:
        totals_rows = totals.receipt_rows(values, lines)

    context.update(
        fields=fields,
        lines=lines,
        statement=statement,
        columns=columns,
        totals_rows=totals_rows,
        debit_total=sum((line.debit or 0 for line in lines), 0),
        credit_total=sum((line.credit or 0 for line in lines), 0),
        amount_total=sum((line.amount or 0 for line in lines), 0),
        issues=issues,
        open_issues=len(open_issues),
        open_errors=len(open_errors),
        open_warnings=len(open_issues) - len(open_errors),
        method_label=dict(ExtractionMethod.choices).get(result.extraction_method, ""),
    )
    return context


def render_review_body(request: HttpRequest, document: Document) -> HttpResponse:
    """The right-hand panels (details, lines, totals, validation), re-rendered."""
    document.refresh_from_db()
    # Only reached after get_document(..., review_action=True) passed, so the user may review.
    context = review_context(document, can_review=True)
    context["clear_messages"] = True  # an edit worked, so remove any old error message
    return render(request, "document_intake/partials/review_body.html", context)


@intake_view
@require_GET
def document_review(request: HttpRequest, pk: Any) -> HttpResponse:
    document = get_document(request, pk)
    can_review = is_allowed(request, document, review_action=True)
    context = review_context(document, can_review=can_review)
    context.update(
        _page_context(
            page_count=_page_count(document),
            page_number=1,
            can_reject=can_review and document.status in CAN_REJECT,
            can_reprocess=can_review and document.status in CAN_REJECT,
            can_export=can_review and document.status in export.EXPORTABLE,
            can_delete=lifecycle.can_delete(document.status),
        )
    )
    return render(request, "document_intake/document_review.html", context)


@intake_view
@require_GET
def review_status(request: HttpRequest, pk: Any) -> HttpResponse:
    """Polled while extraction runs. 204 = still running (HTMX does nothing)."""
    document = get_document(request, pk)
    if document.status in IN_PROGRESS:
        return HttpResponse(status=204)
    return refresh_page()


# --------------------------------------------------------------- edits (re-render panels)


def _edit(request: HttpRequest, pk: Any, change: Callable[[Document], Any]) -> HttpResponse:
    """Run one edit through the review service, then send back the fresh panels."""
    document = get_document(request, pk, review_action=True)
    try:
        change(document)
    except IntakeError as exc:
        return show_message(request, str(exc))
    except ObjectDoesNotExist:
        raise Http404("Not found.") from None
    return render_review_body(request, document)


@intake_view
@require_POST
def edit_field(request: HttpRequest, pk: Any, field_id: Any) -> HttpResponse:
    form = FieldForm(request.POST)
    if not form.is_valid():
        return show_message(request, "The value is too long.")
    return _edit(
        request,
        pk,
        lambda doc: review.edit_field(doc, field_id, form.cleaned_data["value"], request.user),
    )


@intake_view
@require_POST
def edit_line(request: HttpRequest, pk: Any, line_id: Any) -> HttpResponse:
    get_document(request, pk, review_action=True)  # 404 / 403 before looking at the values
    form = LineForm(request.POST)
    if not form.is_valid():
        return show_message(request, f"Not saved. {form.error_text()}")
    values = form.sent_values(request.POST)
    return _edit(request, pk, lambda doc: review.edit_line(doc, line_id, values, request.user))


@intake_view
@require_POST
def add_line(request: HttpRequest, pk: Any) -> HttpResponse:
    return _edit(request, pk, lambda doc: review.add_line(doc, {}, request.user))


@intake_view
@require_POST
def delete_line(request: HttpRequest, pk: Any, line_id: Any) -> HttpResponse:
    return _edit(request, pk, lambda doc: review.delete_line(doc, line_id, request.user))


@intake_view
@require_POST
def resolve_issue(request: HttpRequest, pk: Any, issue_id: Any) -> HttpResponse:
    resolved = request.POST.get("resolved") == "1"
    return _edit(
        request,
        pk,
        lambda doc: review.set_issue_resolved(doc, issue_id, resolved, request.user),
    )


# ------------------------------------------------------- actions (reload the whole page)


def _action(request: HttpRequest, pk: Any, change: Callable[[Document], Any]) -> HttpResponse:
    """Run one status change; on success reload the page so every part shows the new status."""
    document = get_document(request, pk, review_action=True)
    try:
        change(document)
    except IntakeError as exc:
        return show_message(request, str(exc))
    return refresh_page()


def _prompt(request: HttpRequest) -> str:
    """Text the user typed into an ``hx-prompt`` box (sent by HTMX in a header)."""
    return request.headers.get("HX-Prompt", "").strip()[:500]


@intake_view
@require_POST
def approve(request: HttpRequest, pk: Any) -> HttpResponse:
    acknowledge = request.POST.get("acknowledge_errors") == "on"
    return _action(
        request,
        pk,
        lambda doc: lifecycle.approve(
            doc, request.user, acknowledge_errors=acknowledge, note=_prompt(request)
        ),
    )


@intake_view
@require_POST
def reject(request: HttpRequest, pk: Any) -> HttpResponse:
    reason = _prompt(request)
    if not reason:
        return show_message(request, "Write a reason to reject the document.")
    return _action(request, pk, lambda doc: lifecycle.reject(doc, request.user, reason=reason))


@intake_view
@require_POST
def clarify(request: HttpRequest, pk: Any) -> HttpResponse:
    question = _prompt(request)
    if not question:
        return show_message(request, "Write the question for the client.")
    return _action(
        request,
        pk,
        lambda doc: lifecycle.request_clarification(doc, request.user, question=question),
    )


@intake_view
@require_POST
def resume_review(request: HttpRequest, pk: Any) -> HttpResponse:
    return _action(request, pk, lambda doc: lifecycle.resume_review(doc, request.user))


@intake_view
@require_POST
def mark_duplicate(request: HttpRequest, pk: Any) -> HttpResponse:
    return _action(
        request,
        pk,
        lambda doc: lifecycle.mark_duplicate(doc, request.user, of=doc.duplicate_of),
    )


@intake_view
@require_POST
def reprocess(request: HttpRequest, pk: Any) -> HttpResponse:
    return _action(request, pk, lambda doc: lifecycle.queue_reprocess(doc, request.user))


# ------------------------------------------------------------ files, preview, activity


def _page_count(document: Document) -> int:
    if document.page_count:
        return int(document.page_count)
    try:
        with document.file.open("rb") as fp:
            return sources.page_count(fp.read(), document.mime_type)
    except Exception:
        return 1


@intake_view
@require_GET
def viewer(request: HttpRequest, pk: Any) -> HttpResponse:
    """The preview panel for one page (Previous / Next swap this panel)."""
    document = get_document(request, pk)
    count = _page_count(document)
    try:
        number = min(max(1, int(request.GET.get("page", "1"))), count)
    except ValueError:
        number = 1
    context = {"document": document, "page_number": number, "page_count": count}
    return render(request, "document_intake/partials/viewer.html", context)


@intake_view
@require_GET
def page_image(request: HttpRequest, pk: Any, number: int) -> HttpResponse:
    """One page rendered to PNG (PDFs and photos alike)."""
    document = get_document(request, pk)
    try:
        with document.file.open("rb") as fp:
            data = fp.read()
        png = sources.render_preview(data, document.mime_type, number, 110)
    except (FileNotFoundError, IndexError):
        raise Http404("No such page.") from None
    response = HttpResponse(png, content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    return response


@intake_view
@require_GET
def original_file(request: HttpRequest, pk: Any) -> FileResponse:
    """The uploaded file, after the permission check. Every view is written to the audit log."""
    document = get_document(request, pk)
    download = request.GET.get("download") == "1"
    audit.record(
        AuditAction.DOWNLOAD,
        document,
        request.user,
        disposition="attachment" if download else "inline",
    )
    response = FileResponse(document.file.open("rb"), content_type=document.mime_type)
    response["Content-Disposition"] = (
        content_disposition_header(download, document.original_filename) or "inline"
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@intake_view
@require_GET
def export_csv(request: HttpRequest, pk: Any) -> HttpResponse:
    document = get_document(request, pk, review_action=True)
    try:
        filename, text = export.export_csv(document, request.user)
    except IntakeError as exc:
        return HttpResponse(str(exc), status=409, content_type="text/plain")
    response = HttpResponse(text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(True, filename) or "attachment"
    response["Cache-Control"] = "private, no-store"
    return response


@intake_view
@require_GET
def activity_list(request: HttpRequest, pk: Any) -> HttpResponse:
    document = get_document(request, pk)
    entries = document.audit_entries.select_related("actor").order_by("-timestamp")[:200]
    rows = [
        {"when": e.timestamp, "who": activity.actor_name(e), "what": activity.describe(e)}
        for e in entries
    ]
    return render(request, "document_intake/partials/activity.html", {"rows": rows})
