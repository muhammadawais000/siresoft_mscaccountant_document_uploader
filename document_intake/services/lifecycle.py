"""Document status lifecycle.

    uploaded ──> processing ──> needs_review ──> approved ──> exported
        │            │              │  ▲   │
        │            └─> failed     │  │   ├─> rejected
        └─> duplicate               │  │   └─> duplicate
                                    ▼  │
                             needs_clarification

``uploaded`` means "queued for extraction"; reprocessing returns a document there.
Extraction output is never approved automatically: only ``approve`` (a person) moves a
document to ``approved``. Every change is locked, validated and written to the audit log.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from document_intake.exceptions import ApprovalBlocked, InvalidTransition
from document_intake.models import (
    AuditAction,
    Document,
    DocumentStatus,
    Severity,
    ValidationIssue,
)
from document_intake.signals import document_approved

from . import audit

logger = logging.getLogger(__name__)

S = DocumentStatus

ALLOWED: dict[str, frozenset[str]] = {
    S.UPLOADED: frozenset({S.PROCESSING, S.FAILED}),
    S.PROCESSING: frozenset({S.PROCESSING, S.NEEDS_REVIEW, S.FAILED}),
    S.NEEDS_REVIEW: frozenset(
        {S.APPROVED, S.REJECTED, S.NEEDS_CLARIFICATION, S.DUPLICATE, S.UPLOADED}
    ),
    S.NEEDS_CLARIFICATION: frozenset({S.NEEDS_REVIEW, S.REJECTED, S.UPLOADED}),
    S.APPROVED: frozenset({S.EXPORTED}),
    S.EXPORTED: frozenset(),
    S.REJECTED: frozenset(),
    S.DUPLICATE: frozenset({S.UPLOADED, S.REJECTED}),
    S.FAILED: frozenset({S.UPLOADED, S.REJECTED}),
}

# Statuses in which the extracted data may still be edited by a reviewer.
EDITABLE = frozenset({S.NEEDS_REVIEW, S.NEEDS_CLARIFICATION})


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED.get(current, frozenset())


def transition(
    document: Document,
    target: str,
    *,
    actor: Any = None,
    action: AuditAction = AuditAction.STATUS_CHANGE,
    error_message: str | None = None,
    **metadata: Any,
) -> Document:
    """Move ``document`` to ``target`` or raise InvalidTransition.

    Locks the row, so concurrent requests cannot both act on the same old status.
    Returns the refreshed, updated document.
    """
    with transaction.atomic():
        locked: Document = Document.objects.select_for_update().get(pk=document.pk)
        if not can_transition(locked.status, target):
            raise InvalidTransition(f"Cannot change status from '{locked.status}' to '{target}'.")
        previous = locked.status
        locked.status = target
        fields = ["status", "updated_at"]
        if error_message is not None:
            locked.error_message = error_message[:500]
            fields.append("error_message")
        locked.save(update_fields=fields)
        audit.record(action, locked, actor, **{"from": previous, "to": target, **metadata})
    logger.info(
        "status changed",
        extra={"document_id": str(locked.id), "from": previous, "to": target},
    )
    return locked


def open_errors(document: Document) -> list[ValidationIssue]:
    """Unresolved error-level issues on the latest extraction result."""
    result = document.latest_result()
    if result is None:
        return []
    return list(result.issues.filter(severity=Severity.ERROR, resolved=False))


def approve(
    document: Document, actor: Any, *, acknowledge_errors: bool = False, note: str = ""
) -> Document:
    """Human approval. Blocked while errors are open unless the reviewer acknowledges them.

    Acknowledging marks those issues resolved by the reviewer and records them in the
    audit log, so the decision is traceable.
    """
    if document.latest_result() is None:
        raise InvalidTransition("There is no extraction result to approve.")
    with transaction.atomic():
        # Lock first so issues cannot change between the check and the approval.
        document = Document.objects.select_for_update().get(pk=document.pk)
        errors = open_errors(document)
        if errors and not acknowledge_errors:
            raise ApprovalBlocked(
                f"{len(errors)} unresolved error(s). Fix them or acknowledge them to approve."
            )
        now = timezone.now()
        for issue in errors:
            issue.resolved, issue.resolved_by, issue.resolved_at = True, actor, now
        ValidationIssue.objects.bulk_update(errors, ["resolved", "resolved_by", "resolved_at"])
        approved = transition(
            document,
            S.APPROVED,
            actor=actor,
            action=AuditAction.APPROVE,
            acknowledged=[i.rule_code for i in errors],
            note=note[:500],
        )
        result = approved.latest_result()
        transaction.on_commit(
            lambda: document_approved.send(
                sender=Document, document=approved, result=result, actor=actor
            )
        )
    return approved


def reject(document: Document, actor: Any, *, reason: str) -> Document:
    if not reason.strip():
        raise InvalidTransition("A reason is required to reject a document.")
    return transition(
        document, S.REJECTED, actor=actor, action=AuditAction.REJECT, reason=reason[:500]
    )


def request_clarification(document: Document, actor: Any, *, question: str) -> Document:
    """Pause review until the client answers. The portal delivers the question."""
    if not question.strip():
        raise InvalidTransition("A question is required.")
    return transition(document, S.NEEDS_CLARIFICATION, actor=actor, question=question[:500])


def resume_review(document: Document, actor: Any) -> Document:
    return transition(document, S.NEEDS_REVIEW, actor=actor)


def mark_duplicate(document: Document, actor: Any, *, of: Document | None = None) -> Document:
    updated = transition(
        document, S.DUPLICATE, actor=actor, duplicate_of=str(of.id) if of else None
    )
    if of is not None and of.pk != updated.pk:
        updated.duplicate_of = of
        updated.save(update_fields=["duplicate_of", "updated_at"])
    return updated


def mark_exported(document: Document, actor: Any) -> Document:
    return transition(document, S.EXPORTED, actor=actor, action=AuditAction.EXPORT)


def queue_reprocess(document: Document, actor: Any) -> Document:
    """Send the document back for extraction; a new result version will be created."""
    updated = transition(document, S.UPLOADED, actor=actor, action=AuditAction.REPROCESS)
    transaction.on_commit(lambda: _enqueue(updated))
    return updated


def _enqueue(document: Document) -> None:
    from document_intake.tasks import extract_document

    extract_document.delay(str(document.id))
