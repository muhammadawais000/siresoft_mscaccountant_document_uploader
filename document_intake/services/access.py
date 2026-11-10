"""Who may see which documents.

Both hooks are swappable through ``DOCUMENT_INTAKE`` so a host portal can apply its own
roles and client assignments:

* ``DOCUMENT_SCOPE`` filters list queries (a function ``(user, queryset) -> queryset``).
* ``PERMISSION_CLASSES`` are DRF permission classes checked per request and per object.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from django.utils.module_loading import import_string

from document_intake.conf import intake_settings
from document_intake.models import Document


def default_document_scope(user: Any, queryset: QuerySet[Document]) -> QuerySet[Document]:
    """Staff see every document; other users only the ones they uploaded."""
    if user.is_staff:
        return queryset
    return queryset.filter(uploaded_by=user)


def visible_documents(user: Any) -> QuerySet[Document]:
    """Live (not soft-deleted) documents the user may see."""
    scope = import_string(intake_settings.DOCUMENT_SCOPE)
    base = Document.objects.select_related("uploaded_by", "duplicate_of")
    result: QuerySet[Document] = scope(user, base)
    return result


def can_access(user: Any, document: Document) -> bool:
    """Object-level check that reuses the configured scope, so the two never disagree."""
    scope = import_string(intake_settings.DOCUMENT_SCOPE)
    return bool(scope(user, Document.objects.filter(pk=document.pk)).exists())
