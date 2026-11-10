"""Append-only audit trail.

Enforced twice: the ORM refuses updates and deletes, and a PostgreSQL trigger
(see migration 0002) rejects UPDATE and DELETE statements from any client.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models

from document_intake.exceptions import IntakeError

from .choices import AuditAction
from .document import Document


class AuditLogImmutable(IntakeError):
    code = "audit_log_immutable"


class AuditLogQuerySet(models.QuerySet["AuditLog"]):
    def update(self, **kwargs: Any) -> int:
        raise AuditLogImmutable("Audit log entries cannot be changed.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AuditLogImmutable("Audit log entries cannot be deleted.")


class AuditLog(models.Model):
    """Who did what to which document. ``actor`` is null for system actions (the worker)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="audit_entries")
    metadata = models.JSONField(default=dict, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ("-timestamp",)
        indexes = [models.Index(fields=["document", "-timestamp"], name="intake_audit_doc_ts")]

    def __str__(self) -> str:
        return f"{self.action} on {self.document_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AuditLogImmutable("Audit log entries cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AuditLogImmutable("Audit log entries cannot be deleted.")
