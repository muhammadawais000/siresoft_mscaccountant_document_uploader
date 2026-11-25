"""API serializers: explicit fields, input shape only. No business logic here."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from document_intake.models import (
    AuditLog,
    DocType,
    Document,
    DocumentStatus,
    ExtractedField,
    ExtractedLine,
    ExtractionResult,
    ValidationIssue,
)
from document_intake.services import card_statement

# ------------------------------------------------------------------ documents


class DocumentUploadSerializer(serializers.Serializer[None]):
    """Multipart upload input. File checks happen in ``services.upload``."""

    file = serializers.FileField(allow_empty_file=True, use_url=False)
    doc_type = serializers.ChoiceField(choices=DocType.choices, default=DocType.AUTO)
    owner_reference = serializers.UUIDField(required=False, allow_null=True, default=None)
    request_reference = serializers.CharField(
        required=False, allow_blank=True, max_length=128, default=""
    )


class DocumentSerializer(serializers.ModelSerializer[Document]):
    uploaded_by: serializers.PrimaryKeyRelatedField[Any] = serializers.PrimaryKeyRelatedField(
        read_only=True
    )
    duplicate_of: serializers.PrimaryKeyRelatedField[Any] = serializers.PrimaryKeyRelatedField(
        read_only=True
    )
    effective_doc_type = serializers.CharField(read_only=True)
    open_issues = serializers.SerializerMethodField()
    open_errors = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = (
            "id",
            "original_filename",
            "mime_type",
            "size_bytes",
            "sha256",
            "doc_type",
            "detected_doc_type",
            "effective_doc_type",
            "status",
            "extraction_method",
            "page_count",
            "error_message",
            "duplicate_of",
            "owner_reference",
            "request_reference",
            "uploaded_by",
            "open_issues",
            "open_errors",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_open_issues(self, obj: Document) -> int:
        return int(getattr(obj, "open_issues", 0) or 0)

    def get_open_errors(self, obj: Document) -> int:
        return int(getattr(obj, "open_errors", 0) or 0)


class DocumentFilterSerializer(serializers.Serializer[None]):
    """Query-string filters for the document list."""

    status = serializers.MultipleChoiceField(choices=DocumentStatus.choices, required=False)
    doc_type = serializers.ChoiceField(choices=DocType.choices, required=False)
    owner_reference = serializers.UUIDField(required=False)
    created_from = serializers.DateField(required=False)
    created_to = serializers.DateField(required=False)
    search = serializers.CharField(required=False, max_length=100)
    ordering = serializers.ChoiceField(
        choices=["created_at", "-created_at", "original_filename", "-original_filename"],
        required=False,
        default="-created_at",
    )


# --------------------------------------------------------------------- result


class ExtractedFieldSerializer(serializers.ModelSerializer[ExtractedField]):
    class Meta:
        model = ExtractedField
        fields = (
            "id",
            "key",
            "label",
            "position",
            "value",
            "normalized_value",
            "status",
            "status_reason",
            "edited_by",
            "edited_at",
        )
        read_only_fields = fields


class ExtractedLineSerializer(serializers.ModelSerializer[ExtractedLine]):
    class Meta:
        model = ExtractedLine
        fields = (
            "id",
            "line_no",
            "date",
            "description",
            "debit",
            "credit",
            "amount",
            "balance",
            "tax",
            "posting_date",
            "transaction_number",
            "auth_number",
            "card_last4",
            "pre_tax",
            "page",
            "category",
            "status",
            "status_reason",
            "edited_by",
            "edited_at",
        )
        read_only_fields = fields


class ValidationIssueSerializer(serializers.ModelSerializer[ValidationIssue]):
    class Meta:
        model = ValidationIssue
        fields = (
            "id",
            "rule_code",
            "severity",
            "field_key",
            "line_no",
            "message",
            "resolved",
            "resolved_by",
            "resolved_at",
        )
        read_only_fields = fields


class ExtractionResultSerializer(serializers.ModelSerializer[ExtractionResult]):
    doc_type = serializers.CharField(source="document.effective_doc_type", read_only=True)
    document_status = serializers.CharField(source="document.status", read_only=True)
    # Not named "fields": that would shadow Serializer.fields.
    header_fields = ExtractedFieldSerializer(source="fields", many=True, read_only=True)
    lines = ExtractedLineSerializer(many=True, read_only=True)
    issues = ValidationIssueSerializer(many=True, read_only=True)

    class Meta:
        model = ExtractionResult
        fields = (
            "id",
            "version",
            "doc_type",
            "document_status",
            "extraction_method",
            "provider",
            "model_name",
            "prompt_version",
            "duration_ms",
            "created_at",
            "header_fields",
            "lines",
            "issues",
        )
        read_only_fields = fields

    def to_representation(self, instance: ExtractionResult) -> dict[str, Any]:
        """Card statements add ``CardReport`` keys; every other result is unchanged."""
        data: dict[str, Any] = super().to_representation(instance)
        report = card_statement.build_report(instance, instance.fields.all(), instance.lines.all())
        if report is not None:
            data.update(report.as_dict())
        return data


class AuditEntrySerializer(serializers.ModelSerializer[AuditLog]):
    actor = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ("id", "timestamp", "action", "actor", "metadata")
        read_only_fields = fields

    def get_actor(self, obj: AuditLog) -> str:
        """Display name; "System" for worker actions."""
        if obj.actor is None:
            return "System"
        full_name = str(getattr(obj.actor, "get_full_name", lambda: "")())
        return full_name or str(obj.actor.get_username())


class PagesSerializer(serializers.Serializer[None]):
    count = serializers.IntegerField()


# ---------------------------------------------------------------- edit inputs


class FieldEditSerializer(serializers.Serializer[None]):
    value = serializers.CharField(allow_blank=True, max_length=500, trim_whitespace=True)


def _money(**kwargs: Any) -> serializers.DecimalField:
    return serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True, **kwargs
    )


class LineEditSerializer(serializers.Serializer[None]):
    """Typed line values. Statement amounts are derived from debit/credit server-side."""

    date = serializers.DateField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    debit = _money(min_value=0)
    credit = _money(min_value=0)
    amount = _money()
    balance = _money()
    tax = _money()
    posting_date = serializers.DateField(required=False, allow_null=True)
    transaction_number = serializers.CharField(required=False, allow_blank=True, max_length=64)
    card_last4 = serializers.RegexField(r"^\d{4}$", required=False, allow_blank=True)
    pre_tax = _money()
    category = serializers.CharField(required=False, allow_blank=True, max_length=128)


class IssueResolveSerializer(serializers.Serializer[None]):
    resolved = serializers.BooleanField()


# --------------------------------------------------------------- action inputs


class ApproveSerializer(serializers.Serializer[None]):
    acknowledge_errors = serializers.BooleanField(default=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class RejectSerializer(serializers.Serializer[None]):
    reason = serializers.CharField(max_length=500)


class ClarifySerializer(serializers.Serializer[None]):
    question = serializers.CharField(max_length=500)


class MarkDuplicateSerializer(serializers.Serializer[None]):
    duplicate_of = serializers.UUIDField(required=False, allow_null=True, default=None)
