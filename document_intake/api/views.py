"""API views. Views stay thin; business logic lives in ``document_intake.services``."""

from __future__ import annotations

from typing import Any

from django.db.models import Prefetch, QuerySet
from django.http import FileResponse, HttpResponse
from django.utils.http import content_disposition_header
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from document_intake.extraction import sources
from document_intake.models import (
    AuditAction,
    Document,
    ExtractedLine,
    ExtractionResult,
)
from document_intake.services import access, audit, export, lifecycle, query, review, upload

from .permissions import configured_permission_classes, configured_review_permission_classes
from .serializers import (
    ApproveSerializer,
    AuditEntrySerializer,
    ClarifySerializer,
    DocumentFilterSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
    ExtractionResultSerializer,
    FieldEditSerializer,
    IssueResolveSerializer,
    LineEditSerializer,
    MarkDuplicateSerializer,
    PagesSerializer,
    RejectSerializer,
)
from .throttles import UploadRateThrottle

# Actions that change review data or status; guarded by REVIEW_PERMISSION_CLASSES too.
REVIEW_ACTIONS = frozenset(
    {
        "edit_field",
        "add_line",
        "edit_line",
        "resolve_issue",
        "reprocess",
        "approve",
        "reject",
        "clarify",
        "resume_review",
        "mark_duplicate",
        "export_csv",
    }
)

UUID_PATTERN = r"[0-9a-fA-F-]{36}"


class HealthView(APIView):
    """Liveness probe for load balancers. Reveals nothing about documents."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    @extend_schema(
        responses=inline_serializer("Health", {"status": serializers.CharField()}),
    )
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


def _result_queryset() -> QuerySet[ExtractionResult]:
    return ExtractionResult.objects.select_related("document").prefetch_related(
        "fields",
        Prefetch("lines", queryset=ExtractedLine.objects.order_by("line_no")),
        "issues",
    )


@extend_schema(tags=["documents"])
class DocumentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[Document],
):
    """Upload, list and review documents. Poll ``retrieve`` for status changes."""

    serializer_class = DocumentSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    lookup_value_regex = UUID_PATTERN

    # ---------------------------------------------------------------- plumbing

    def get_permissions(self) -> list[BasePermission]:
        classes = configured_permission_classes()
        if self.action in REVIEW_ACTIONS:
            classes += configured_review_permission_classes()
        return [cls() for cls in classes]

    def get_queryset(self) -> QuerySet[Document]:
        documents = access.visible_documents(self.request.user)
        if self.action == "list":
            filters = DocumentFilterSerializer(data=self.request.query_params)
            filters.is_valid(raise_exception=True)
            documents = query.filter_documents(documents, filters.validated_data)
        if self.action in ("list", "retrieve"):
            documents = query.with_issue_counts(documents)
        return documents

    def get_throttles(self) -> list[BaseThrottle]:
        if self.action == "create":
            return [UploadRateThrottle()]
        return super().get_throttles()

    def _result_response(self, document: Document, http_status: int = 200) -> Response:
        result = _result_queryset().filter(document=document).order_by("-version").first()
        if result is None:
            raise NotFound("This document has no extraction result yet.")
        return Response(ExtractionResultSerializer(result).data, status=http_status)

    def _document_response(self, document: Document) -> Response:
        fresh = query.with_issue_counts(Document.objects.filter(pk=document.pk)).get()
        return Response(DocumentSerializer(fresh).data)

    # ---------------------------------------------------------- list / create

    @extend_schema(parameters=[DocumentFilterSerializer])
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request={"multipart/form-data": DocumentUploadSerializer},
        responses={
            201: DocumentSerializer,
            400: OpenApiResponse(description="Validation failed (type, size or content)."),
        },
    )
    def create(self, request: Request) -> Response:
        payload = DocumentUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        document = upload.create_document(
            upload=data["file"],
            uploaded_by=request.user,
            doc_type=data["doc_type"],
            owner_reference=data["owner_reference"],
            request_reference=data["request_reference"],
        )
        return Response(DocumentSerializer(document).data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------ result

    @extend_schema(
        parameters=[OpenApiParameter("version", int, description="Defaults to the latest.")],
        responses=ExtractionResultSerializer,
    )
    @action(detail=True, methods=["get"])
    def result(self, request: Request, pk: str | None = None) -> Response:
        """Fields, lines and issues of the latest (or a given) extraction version."""
        document = self.get_object()
        version = request.query_params.get("version")
        if version is None:
            return self._result_response(document)
        if not version.isdigit():
            raise NotFound("Unknown version.")
        result = _result_queryset().filter(document=document, version=int(version)).first()
        if result is None:
            raise NotFound("Unknown version.")
        return Response(ExtractionResultSerializer(result).data)

    @extend_schema(request=FieldEditSerializer, responses=ExtractionResultSerializer)
    @action(
        detail=True,
        methods=["patch"],
        url_path=rf"fields/(?P<field_id>{UUID_PATTERN})",
        url_name="field",
    )
    def edit_field(self, request: Request, pk: str | None = None, field_id: str = "") -> Response:
        document = self.get_object()
        payload = FieldEditSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        review.edit_field(document, field_id, payload.validated_data["value"], request.user)
        return self._result_response(document)

    @extend_schema(request=LineEditSerializer, responses={201: ExtractionResultSerializer})
    @action(detail=True, methods=["post"], url_path="lines", url_name="lines")
    def add_line(self, request: Request, pk: str | None = None) -> Response:
        document = self.get_object()
        payload = LineEditSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        review.add_line(document, payload.validated_data, request.user)
        return self._result_response(document, status.HTTP_201_CREATED)

    @extend_schema(
        methods=["PATCH"], request=LineEditSerializer, responses=ExtractionResultSerializer
    )
    @extend_schema(methods=["DELETE"], request=None, responses=ExtractionResultSerializer)
    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=rf"lines/(?P<line_id>{UUID_PATTERN})",
        url_name="line",
    )
    def edit_line(self, request: Request, pk: str | None = None, line_id: str = "") -> Response:
        document = self.get_object()
        if request.method == "DELETE":
            review.delete_line(document, line_id, request.user)
            return self._result_response(document)
        payload = LineEditSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        review.edit_line(document, line_id, payload.validated_data, request.user)
        return self._result_response(document)

    @extend_schema(request=IssueResolveSerializer, responses=ExtractionResultSerializer)
    @action(
        detail=True,
        methods=["patch"],
        url_path=rf"issues/(?P<issue_id>{UUID_PATTERN})",
        url_name="issue",
    )
    def resolve_issue(
        self, request: Request, pk: str | None = None, issue_id: str = ""
    ) -> Response:
        document = self.get_object()
        payload = IssueResolveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        review.set_issue_resolved(
            document, issue_id, payload.validated_data["resolved"], request.user
        )
        return self._result_response(document)

    # ----------------------------------------------------------------- actions

    @extend_schema(request=None, responses=DocumentSerializer)
    @action(detail=True, methods=["post"])
    def reprocess(self, request: Request, pk: str | None = None) -> Response:
        """Queue a new extraction run (creates result version n+1)."""
        return self._document_response(lifecycle.queue_reprocess(self.get_object(), request.user))

    @extend_schema(
        request=ApproveSerializer,
        responses={
            200: DocumentSerializer,
            409: OpenApiResponse(description="Open errors not acknowledged, or wrong status."),
        },
    )
    @action(detail=True, methods=["post"])
    def approve(self, request: Request, pk: str | None = None) -> Response:
        payload = ApproveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        document = lifecycle.approve(self.get_object(), request.user, **payload.validated_data)
        return self._document_response(document)

    @extend_schema(request=RejectSerializer, responses=DocumentSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request: Request, pk: str | None = None) -> Response:
        payload = RejectSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        document = lifecycle.reject(
            self.get_object(), request.user, reason=payload.validated_data["reason"]
        )
        return self._document_response(document)

    @extend_schema(request=ClarifySerializer, responses=DocumentSerializer)
    @action(detail=True, methods=["post"])
    def clarify(self, request: Request, pk: str | None = None) -> Response:
        """Pause review with a question for the client (status needs_clarification)."""
        payload = ClarifySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        document = lifecycle.request_clarification(
            self.get_object(), request.user, question=payload.validated_data["question"]
        )
        return self._document_response(document)

    @extend_schema(request=None, responses=DocumentSerializer)
    @action(detail=True, methods=["post"], url_path="resume-review", url_name="resume-review")
    def resume_review(self, request: Request, pk: str | None = None) -> Response:
        return self._document_response(lifecycle.resume_review(self.get_object(), request.user))

    @extend_schema(request=MarkDuplicateSerializer, responses=DocumentSerializer)
    @action(detail=True, methods=["post"], url_path="mark-duplicate", url_name="mark-duplicate")
    def mark_duplicate(self, request: Request, pk: str | None = None) -> Response:
        payload = MarkDuplicateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        original = None
        if original_id := payload.validated_data["duplicate_of"]:
            original = access.visible_documents(request.user).filter(pk=original_id).first()
            if original is None:
                raise NotFound("The original document was not found.")
        document = lifecycle.mark_duplicate(self.get_object(), request.user, of=original)
        return self._document_response(document)

    # ------------------------------------------------------------ preview / activity

    def _file_bytes(self, document: Document) -> bytes:
        with document.file.open("rb") as fp:
            data: bytes = fp.read()
        return data

    @extend_schema(responses=PagesSerializer)
    @action(detail=True, methods=["get"])
    def pages(self, request: Request, pk: str | None = None) -> Response:
        """How many pages the viewer can show."""
        document = self.get_object()
        count = document.page_count or sources.page_count(
            self._file_bytes(document), document.mime_type
        )
        return Response({"count": count})

    @extend_schema(
        operation_id="documents_page_image",
        parameters=[OpenApiParameter("dpi", int, description="72-200, default 110.")],
        responses={(200, "image/png"): OpenApiTypes.BINARY},
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"pages/(?P<page_number>[0-9]{1,4})",
        url_name="page",
    )
    def page(self, request: Request, pk: str | None = None, page_number: str = "1") -> HttpResponse:
        """One page rendered to PNG for the review screen (PDFs and images alike)."""
        document = self.get_object()
        try:
            dpi = min(200, max(72, int(request.query_params.get("dpi", "110"))))
        except ValueError:
            dpi = 110
        try:
            png = sources.render_preview(
                self._file_bytes(document), document.mime_type, int(page_number), dpi
            )
        except IndexError as exc:
            raise NotFound("No such page.") from exc
        response = HttpResponse(png, content_type="image/png")
        response["Cache-Control"] = "private, no-store"
        return response

    @extend_schema(responses=AuditEntrySerializer(many=True))
    @action(detail=True, methods=["get"])
    def activity(self, request: Request, pk: str | None = None) -> Response:
        """The document's audit trail, newest first (latest 200 entries)."""
        document = self.get_object()
        entries = document.audit_entries.select_related("actor").order_by("-timestamp")[:200]
        return Response(AuditEntrySerializer(entries, many=True).data)

    # ------------------------------------------------------------ files / export

    @extend_schema(
        parameters=[OpenApiParameter("download", bool, description="Attachment, not inline.")],
        responses={(200, "*/*"): OpenApiTypes.BINARY},
    )
    @action(detail=True, methods=["get"])
    def file(self, request: Request, pk: str | None = None) -> FileResponse:
        """The original file, only after the permission check. Never a public URL."""
        document = self.get_object()
        as_attachment = request.query_params.get("download") in ("1", "true")
        audit.record(
            AuditAction.DOWNLOAD,
            document,
            request.user,
            disposition="attachment" if as_attachment else "inline",
        )
        response = FileResponse(
            document.file.open("rb"),
            content_type=document.mime_type,
            as_attachment=as_attachment,
        )
        response["Content-Disposition"] = (
            content_disposition_header(as_attachment, document.original_filename) or "inline"
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        # Allow the review screen (same origin) to embed the preview; nobody else.
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @extend_schema(
        responses={
            (200, "text/csv"): OpenApiTypes.STR,
            409: OpenApiResponse(description="Not approved."),
        },
    )
    @action(detail=True, methods=["get"], url_path=r"export\.csv", url_name="export-csv")
    def export_csv(self, request: Request, pk: str | None = None) -> HttpResponse:
        """Approved data as CSV. The first export moves the document to ``exported``."""
        filename, content = export.export_csv(self.get_object(), request.user)
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = content_disposition_header(True, filename) or ""
        response["Cache-Control"] = "private, no-store"
        return response
