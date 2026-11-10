"""The uploaded document and where its file lives."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from django.conf import settings
from django.core.files.storage import Storage, storages
from django.db import models
from django.utils import timezone

from document_intake.conf import intake_settings

from .base import SoftDeleteModel
from .choices import DocType, DocumentStatus, ExtractionMethod


def intake_storage() -> Storage:
    """Private storage for documents. Callable so the alias can change without a migration."""
    return storages[intake_settings.STORAGE_ALIAS]


def document_upload_to(instance: Document, filename: str) -> str:
    """Build the stored path from server-side values only.

    The client's filename never becomes part of the path (it is kept in
    ``original_filename``), so path traversal and name collisions are impossible.
    """
    suffix = PurePosixPath(filename).suffix.lower()
    allowed = {ext for exts in intake_settings.ALLOWED_MIME_TYPES.values() for ext in exts}
    if suffix not in allowed:
        suffix = ""
    now = timezone.now()
    prefix = intake_settings.UPLOAD_PATH_PREFIX
    return f"{prefix}/{now:%Y}/{now:%m}/{instance.id}{suffix}"


class Document(SoftDeleteModel):
    """An accounting document submitted for extraction and human review.

    ``owner_reference`` and ``request_reference`` are plain values, not foreign keys,
    so a host portal can link documents to its own Client / DocumentRequest records
    without our migrations depending on theirs.
    """

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="intake_documents",
    )
    owner_reference = models.UUIDField(
        null=True,
        blank=True,
        help_text="Owning client/company ID in the host portal.",
    )
    request_reference = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Host portal document-request (or period) reference, if any.",
    )

    original_filename = models.CharField(max_length=255)
    file = models.FileField(storage=intake_storage, upload_to=document_upload_to, max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="duplicates",
    )

    doc_type = models.CharField(max_length=32, choices=DocType.choices, default=DocType.AUTO)
    detected_doc_type = models.CharField(
        max_length=32, choices=DocType.choices, blank=True, default=""
    )
    status = models.CharField(
        max_length=32, choices=DocumentStatus.choices, default=DocumentStatus.UPLOADED
    )
    extraction_method = models.CharField(
        max_length=16, choices=ExtractionMethod.choices, blank=True, default=""
    )
    page_count = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Operator-facing reason for failure. Never contains document contents.",
    )

    class Meta(SoftDeleteModel.Meta):
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["owner_reference", "status"], name="intake_doc_owner_status"),
            models.Index(fields=["sha256"], name="intake_doc_sha256"),
            models.Index(fields=["created_at"], name="intake_doc_created"),
            models.Index(fields=["uploaded_by", "status"], name="intake_doc_uploader_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=DocumentStatus.values),
                name="intake_doc_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(doc_type__in=DocType.values),
                name="intake_doc_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="intake_doc_sha256_format",
            ),
            models.CheckConstraint(
                condition=~models.Q(duplicate_of=models.F("id")),
                name="intake_doc_not_own_duplicate",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status})"

    @property
    def effective_doc_type(self) -> str:
        """The user's choice, or the detected type when the user chose auto-detect."""
        if self.doc_type == DocType.AUTO:
            return self.detected_doc_type
        return self.doc_type

    def latest_result(self) -> Any:
        return self.results.order_by("-version").first()
