"""API serializers: explicit fields, input shape only. No business logic here."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from document_intake.models import DocType, Document, DocumentStatus


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
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


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
