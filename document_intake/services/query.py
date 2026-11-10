"""Read-side helpers for listing documents."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, IntegerField, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce

from document_intake.models import Document, ExtractionResult, Severity, ValidationIssue


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


def with_issue_counts(queryset: QuerySet[Document]) -> QuerySet[Document]:
    """Annotate ``open_issues`` and ``open_errors`` on each document's latest result.

    Two correlated subqueries per row instead of one query per document (no N+1).
    """
    latest = ExtractionResult.objects.filter(document=OuterRef("pk")).order_by("-version")
    queryset = queryset.annotate(latest_result_id=Subquery(latest.values("id")[:1]))

    def count(**filters: Any) -> Coalesce:
        counted = (
            ValidationIssue.objects.filter(
                result=OuterRef("latest_result_id"), resolved=False, **filters
            )
            .order_by()
            .values("result")
            .annotate(n=Count("id"))
            .values("n")
        )
        return Coalesce(Subquery(counted, output_field=IntegerField()), 0)

    return queryset.annotate(open_issues=count(), open_errors=count(severity=Severity.ERROR))
