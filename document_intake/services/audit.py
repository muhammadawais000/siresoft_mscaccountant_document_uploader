"""Audit trail writer. Every state-changing service calls ``record``."""

from __future__ import annotations

from typing import Any

from document_intake.models import AuditAction, AuditLog, Document


def record(
    action: AuditAction,
    document: Document,
    actor: Any | None = None,
    **metadata: Any,
) -> AuditLog:
    """Append one audit entry.

    ``actor`` is the acting user, or None for system work (the Celery worker).
    ``metadata`` must be JSON-serialisable. It is stored in the database (never in
    application logs), so field edits may record old and new values for accountability.
    """
    return AuditLog.objects.create(
        action=action,
        document=document,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        metadata=metadata,
    )
