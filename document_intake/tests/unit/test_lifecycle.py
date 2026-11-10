"""Step 5: status lifecycle, approval rules and signals."""

from unittest.mock import patch

import pytest

from document_intake.exceptions import ApprovalBlocked, InvalidTransition
from document_intake.models import AuditAction, AuditLog, DocumentStatus, Severity
from document_intake.services import lifecycle
from document_intake.signals import document_approved
from document_intake.tests.factories import (
    DocumentFactory,
    ExtractionResultFactory,
    UserFactory,
    ValidationIssueFactory,
)

pytestmark = pytest.mark.django_db
S = DocumentStatus

ALL = list(S.values)


@pytest.mark.parametrize("current", ALL)
@pytest.mark.parametrize("target", ALL)
def test_transition_table_is_enforced(current, target) -> None:
    doc = DocumentFactory(status=current)
    if lifecycle.can_transition(current, target):
        assert lifecycle.transition(doc, target).status == target
    else:
        with pytest.raises(InvalidTransition):
            lifecycle.transition(doc, target)
        doc.refresh_from_db()
        assert doc.status == current


def test_terminal_states() -> None:
    assert lifecycle.ALLOWED[S.EXPORTED] == frozenset()
    assert lifecycle.ALLOWED[S.REJECTED] == frozenset()


def test_nothing_reaches_approved_except_from_review() -> None:
    sources = [s for s, targets in lifecycle.ALLOWED.items() if S.APPROVED in targets]
    assert sources == [S.NEEDS_REVIEW]


def test_transition_is_audited() -> None:
    user = UserFactory()
    doc = DocumentFactory(status=S.NEEDS_REVIEW)
    lifecycle.transition(doc, S.NEEDS_CLARIFICATION, actor=user, question="q")
    entry = AuditLog.objects.get(document=doc)
    assert entry.action == AuditAction.STATUS_CHANGE
    assert entry.actor == user
    assert entry.metadata == {"from": "needs_review", "to": "needs_clarification", "question": "q"}


def test_transition_uses_current_db_status_not_stale_object() -> None:
    doc = DocumentFactory(status=S.NEEDS_REVIEW)
    stale = type(doc).objects.get(pk=doc.pk)
    lifecycle.transition(doc, S.REJECTED)
    with pytest.raises(InvalidTransition):
        lifecycle.transition(stale, S.APPROVED)  # stale copy still says needs_review


# ------------------------------------------------------------------- approve


def reviewable(errors: int = 0, warnings: int = 0):
    doc = DocumentFactory(status=S.NEEDS_REVIEW)
    result = ExtractionResultFactory(document=doc, version=1)
    for i in range(errors):
        ValidationIssueFactory(result=result, severity=Severity.ERROR, rule_code=f"e{i}")
    for i in range(warnings):
        ValidationIssueFactory(result=result, severity=Severity.WARNING, rule_code=f"w{i}")
    return doc, result


def test_approve_clean_document() -> None:
    user = UserFactory()
    doc, result = reviewable(warnings=2)  # warnings never block
    received = []
    document_approved.connect(lambda **kw: received.append(kw), weak=False, dispatch_uid="a")
    try:
        with patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            approved = lifecycle.approve(doc, user, note="looks right")
    finally:
        document_approved.disconnect(dispatch_uid="a")

    assert approved.status == S.APPROVED
    entry = AuditLog.objects.get(document=doc, action=AuditAction.APPROVE)
    assert entry.actor == user
    assert entry.metadata["acknowledged"] == []
    assert entry.metadata["note"] == "looks right"
    assert received[0]["actor"] == user
    assert received[0]["result"] == result


def test_approve_blocked_by_open_errors() -> None:
    doc, _ = reviewable(errors=2)
    with pytest.raises(ApprovalBlocked, match="2 unresolved"):
        lifecycle.approve(doc, UserFactory())
    doc.refresh_from_db()
    assert doc.status == S.NEEDS_REVIEW


def test_approve_with_acknowledgement_resolves_errors() -> None:
    user = UserFactory()
    doc, result = reviewable(errors=2)
    lifecycle.approve(doc, user, acknowledge_errors=True)
    assert not result.issues.filter(resolved=False).exists()
    assert set(result.issues.values_list("resolved_by", flat=True)) == {user.pk}
    entry = AuditLog.objects.get(document=doc, action=AuditAction.APPROVE)
    assert sorted(entry.metadata["acknowledged"]) == ["e0", "e1"]


def test_resolved_errors_do_not_block() -> None:
    doc, result = reviewable(errors=1)
    result.issues.update(resolved=True)
    assert lifecycle.approve(doc, UserFactory()).status == S.APPROVED


def test_only_latest_result_counts() -> None:
    doc, _ = reviewable(errors=1)
    ExtractionResultFactory(document=doc, version=2)  # re-run came back clean
    assert lifecycle.approve(doc, UserFactory()).status == S.APPROVED


def test_approve_needs_a_result() -> None:
    with pytest.raises(InvalidTransition, match="no extraction result"):
        lifecycle.approve(DocumentFactory(status=S.NEEDS_REVIEW), UserFactory())


@pytest.mark.parametrize("status", [S.UPLOADED, S.PROCESSING, S.APPROVED, S.REJECTED, S.FAILED])
def test_approve_wrong_state(status) -> None:
    doc = DocumentFactory(status=status)
    ExtractionResultFactory(document=doc, version=1)
    with pytest.raises(InvalidTransition):
        lifecycle.approve(doc, UserFactory())


# ---------------------------------------------------------- other actions


def test_reject_requires_reason() -> None:
    doc = DocumentFactory(status=S.NEEDS_REVIEW)
    with pytest.raises(InvalidTransition, match="reason"):
        lifecycle.reject(doc, UserFactory(), reason="  ")
    rejected = lifecycle.reject(doc, UserFactory(), reason="Wrong client")
    assert rejected.status == S.REJECTED
    entry = AuditLog.objects.get(document=doc, action=AuditAction.REJECT)
    assert entry.metadata["reason"] == "Wrong client"


def test_clarification_round_trip() -> None:
    user = UserFactory()
    doc = DocumentFactory(status=S.NEEDS_REVIEW)
    with pytest.raises(InvalidTransition):
        lifecycle.request_clarification(doc, user, question="")
    assert lifecycle.request_clarification(doc, user, question="What is this?").status == (
        S.NEEDS_CLARIFICATION
    )
    assert lifecycle.resume_review(doc, user).status == S.NEEDS_REVIEW


def test_mark_duplicate_links_original() -> None:
    original = DocumentFactory()
    doc = DocumentFactory(status=S.NEEDS_REVIEW, uploaded_by=original.uploaded_by)
    updated = lifecycle.mark_duplicate(doc, UserFactory(), of=original)
    assert updated.status == S.DUPLICATE
    assert updated.duplicate_of == original


def test_mark_exported_only_after_approval() -> None:
    with pytest.raises(InvalidTransition):
        lifecycle.mark_exported(DocumentFactory(status=S.NEEDS_REVIEW), UserFactory())
    assert lifecycle.mark_exported(DocumentFactory(status=S.APPROVED), UserFactory()).status == (
        S.EXPORTED
    )


@pytest.mark.parametrize("status", [S.NEEDS_REVIEW, S.FAILED, S.DUPLICATE, S.NEEDS_CLARIFICATION])
def test_reprocess_requeues(status, django_capture_on_commit_callbacks) -> None:
    doc = DocumentFactory(status=status)
    with (
        patch("document_intake.tasks.extract_document.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        updated = lifecycle.queue_reprocess(doc, UserFactory())
    assert updated.status == S.UPLOADED
    delay.assert_called_once_with(str(doc.id))
    assert AuditLog.objects.filter(document=doc, action=AuditAction.REPROCESS).exists()


@pytest.mark.parametrize("status", [S.APPROVED, S.EXPORTED, S.REJECTED, S.PROCESSING])
def test_reprocess_not_allowed(status) -> None:
    with pytest.raises(InvalidTransition):
        lifecycle.queue_reprocess(DocumentFactory(status=status), UserFactory())
