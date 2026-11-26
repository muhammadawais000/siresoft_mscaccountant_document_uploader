"""Receipts attached to card statement purchases, as proof that the purchase happened."""

from __future__ import annotations

from pathlib import PurePosixPath

from django.conf import settings
from django.db import models
from django.utils import timezone

from document_intake.conf import intake_settings

from .base import SoftDeleteModel
from .document import intake_storage
from .extraction import ExtractedLine


def receipt_upload_to(instance: TransactionReceipt, filename: str) -> str:
    """Server-side path only, like documents: the client's filename is never part of it."""
    suffix = PurePosixPath(filename).suffix.lower()
    allowed = {ext for exts in intake_settings.ALLOWED_MIME_TYPES.values() for ext in exts}
    if suffix not in allowed:
        suffix = ""
    now = timezone.now()
    prefix = intake_settings.UPLOAD_PATH_PREFIX
    return f"{prefix}/receipts/{now:%Y}/{now:%m}/{instance.id}{suffix}"


class TransactionReceipt(SoftDeleteModel):
    """A receipt file for one purchase line. Removing it is a soft delete."""

    line = models.ForeignKey(ExtractedLine, on_delete=models.PROTECT, related_name="receipts")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    original_filename = models.CharField(max_length=255)
    file = models.FileField(storage=intake_storage, upload_to=receipt_upload_to, max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)

    class Meta(SoftDeleteModel.Meta):
        ordering = ("created_at",)

    def __str__(self) -> str:
        return self.original_filename
