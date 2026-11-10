"""Read-side helpers for listing documents."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from document_intake.models import Document


def filter_documents(queryset: QuerySet[Document], filters: dict[str, Any]) -> QuerySet[Document]:
    """Apply validated list filters (see ``DocumentFilterSerializer``)."""
    if statuses := filters.get("status"):
        queryset = queryset.filter(status__in=statuses)
    if doc_type := filters.get("doc_type"):
        queryset = queryset.filter(doc_type=doc_type)
    if owner := filters.get("owner_reference"):
        queryset = queryset.filter(owner_reference=owner)
    if created_from := filters.get("created_from"):
        queryset = queryset.filter(created_at__date__gte=created_from)
    if created_to := filters.get("created_to"):
        queryset = queryset.filter(created_at__date__lte=created_to)
    if search := filters.get("search"):
        queryset = queryset.filter(original_filename__icontains=search)
    return queryset.order_by(filters.get("ordering", "-created_at"), "id")
