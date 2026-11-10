"""UI views: render page shells and configuration; all data loads through the REST API."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.urls import NoReverseMatch, reverse
from django.views.generic import TemplateView
from rest_framework.settings import api_settings

from document_intake.conf import intake_settings
from document_intake.models import DocType, DocumentStatus
from document_intake.services import access

PLACEHOLDER_ID = "00000000-0000-0000-0000-000000000000"


def _api_base() -> str:
    return reverse("document_intake_api:health").removesuffix("health/")


def _logout_url() -> str | None:
    """The host project's logout URL, if it has one named ``logout``."""
    try:
        return reverse("logout")
    except NoReverseMatch:
        return None


class IntakePageMixin(LoginRequiredMixin):
    nav_active = "documents"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context: dict[str, Any] = super().get_context_data(**kwargs)  # type: ignore[misc]
        context.update(
            nav_active=self.nav_active,
            intake_api_base=_api_base(),
            logout_url=_logout_url(),
            statuses=DocumentStatus.choices,
            doc_types=DocType.choices,
        )
        return context


class DocumentListView(IntakePageMixin, TemplateView):
    template_name = "document_intake/document_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        allowed: dict[str, tuple[str, ...]] = intake_settings.ALLOWED_MIME_TYPES
        extensions = sorted({ext for exts in allowed.values() for ext in exts})
        max_bytes = int(intake_settings.MAX_UPLOAD_BYTES)
        context.update(
            accept=",".join([*extensions, *allowed]),
            allowed_extensions_text=", ".join(e.lstrip(".").upper() for e in extensions),
            max_upload_mb=max_bytes // (1024 * 1024),
            page_config={
                "statuses": dict(DocumentStatus.choices),
                "docTypes": dict(DocType.choices),
                "reviewUrlTemplate": reverse(
                    "document_intake:document-review", args=[PLACEHOLDER_ID]
                ),
                "fileUrlTemplate": reverse(
                    "document_intake_api:document-file", args=[PLACEHOLDER_ID]
                ),
                "maxUploadBytes": max_bytes,
                "allowedExtensions": extensions,
                "pollMs": 4000,
                "pageSize": int(api_settings.PAGE_SIZE or 25),
            },
        )
        return context


class DocumentReviewView(IntakePageMixin, TemplateView):
    template_name = "document_intake/document_review.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        document = access.visible_documents(self.request.user).filter(pk=kwargs["pk"]).first()
        if document is None:
            raise Http404("Document not found.")
        context["document"] = document
        return context
