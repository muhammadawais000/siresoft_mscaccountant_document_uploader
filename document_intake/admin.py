"""Read-mostly admin. Changes to documents go through the services, not the admin."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from .models import (
    AuditLog,
    Document,
    ExtractedField,
    ExtractedLine,
    ExtractionResult,
    ValidationIssue,
)


class ReadOnlyAdminMixin:
    """No add, change or delete from the admin; viewing only."""

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class ReadOnlyInline(ReadOnlyAdminMixin, admin.TabularInline):  # type: ignore[type-arg]
    extra = 0
    show_change_link = True


class ExtractionResultInline(ReadOnlyInline):
    model = ExtractionResult
    fields = ("version", "extraction_method", "provider", "model_name", "prompt_version")
    readonly_fields = fields


class AuditLogInline(ReadOnlyInline):
    model = AuditLog
    fields = ("timestamp", "action", "actor")
    readonly_fields = fields


@admin.register(Document)
class DocumentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "original_filename",
        "doc_type",
        "status",
        "uploaded_by",
        "created_at",
        "deleted_at",
    )
    list_filter = ("status", "doc_type", "extraction_method", "created_at")
    search_fields = ("original_filename", "sha256", "id", "owner_reference")
    list_select_related = ("uploaded_by",)
    # ``file`` is replaced by ``stored_path``: the admin would otherwise ask the private
    # storage for a public URL, which it deliberately refuses to build.
    exclude = ("file",)
    readonly_fields = [f.name for f in Document._meta.fields if f.name != "file"] + ["stored_path"]
    inlines = (ExtractionResultInline, AuditLogInline)
    date_hierarchy = "created_at"

    @admin.display(description="Stored path")
    def stored_path(self, obj: Document) -> str:
        return obj.file.name or ""

    def get_queryset(self, request: HttpRequest) -> QuerySet[Document]:
        # Staff need to see soft-deleted documents too.
        return Document.all_objects.select_related("uploaded_by")


class ExtractedFieldInline(ReadOnlyInline):
    model = ExtractedField
    fields = ("key", "value", "normalized_value", "status", "status_reason", "edited_by")
    readonly_fields = fields


class ExtractedLineInline(ReadOnlyInline):
    model = ExtractedLine
    fields = ("line_no", "date", "description", "debit", "credit", "amount", "balance", "status")
    readonly_fields = fields


class ValidationIssueInline(ReadOnlyInline):
    model = ValidationIssue
    fields = ("severity", "rule_code", "field_key", "line_no", "message", "resolved")
    readonly_fields = fields


@admin.register(ExtractionResult)
class ExtractionResultAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("document", "version", "extraction_method", "model_name", "created_at")
    list_filter = ("extraction_method", "provider", "model_name")
    list_select_related = ("document",)
    readonly_fields = [f.name for f in ExtractionResult._meta.fields]
    inlines = (ExtractedFieldInline, ExtractedLineInline, ValidationIssueInline)


@admin.register(ValidationIssue)
class ValidationIssueAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("rule_code", "severity", "result", "field_key", "line_no", "resolved")
    list_filter = ("severity", "resolved", "rule_code")
    list_select_related = ("result",)
    readonly_fields = [f.name for f in ValidationIssue._meta.fields]


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("timestamp", "action", "document", "actor")
    list_filter = ("action", "timestamp")
    list_select_related = ("document", "actor")
    search_fields = ("document__id",)
    readonly_fields = [f.name for f in AuditLog._meta.fields]
    date_hierarchy = "timestamp"


@admin.register(ExtractedField)
class ExtractedFieldAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("key", "status", "result", "edited_by", "edited_at")
    list_filter = ("status", "key")
    list_select_related = ("result", "edited_by")
    readonly_fields = [f.name for f in ExtractedField._meta.fields]


@admin.register(ExtractedLine)
class ExtractedLineAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("line_no", "date", "status", "result", "deleted_at")
    list_filter = ("status",)
    list_select_related = ("result",)
    readonly_fields = [f.name for f in ExtractedLine._meta.fields]

    def get_queryset(self, request: HttpRequest) -> QuerySet[ExtractedLine]:
        return ExtractedLine.all_objects.select_related("result")
