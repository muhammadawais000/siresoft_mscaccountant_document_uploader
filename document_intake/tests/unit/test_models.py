"""Step 2: models, constraints, soft delete, audit immutability, admin."""

from decimal import Decimal

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.urls import reverse

from document_intake.models import (
    AuditAction,
    AuditLog,
    AuditLogImmutable,
    DocType,
    Document,
    ExtractedLine,
    HardDeleteForbidden,
)
from document_intake.services import audit
from document_intake.tests.factories import (
    AuditLogFactory,
    DocumentFactory,
    ExtractedFieldFactory,
    ExtractedLineFactory,
    ExtractionResultFactory,
    UserFactory,
    ValidationIssueFactory,
)

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------ Document


def test_stored_path_ignores_client_filename() -> None:
    doc = DocumentFactory(original_filename="../../etc/passwd.PDF")
    assert doc.file.name.startswith("intake/")
    assert doc.file.name.endswith(f"{doc.id}.pdf")
    assert ".." not in doc.file.name
    assert doc.original_filename == "../../etc/passwd.PDF"


def test_unknown_extension_is_dropped_from_stored_path() -> None:
    doc = DocumentFactory(original_filename="invoice.exe")
    assert doc.file.name.endswith(str(doc.id))


def test_private_storage_has_no_public_url() -> None:
    doc = DocumentFactory()
    with pytest.raises(NotImplementedError):
        _ = doc.file.url


def test_sha256_must_be_lowercase_hex() -> None:
    with pytest.raises(IntegrityError):
        DocumentFactory(sha256="not-a-hash")


def test_document_cannot_be_its_own_duplicate() -> None:
    doc = DocumentFactory()
    doc.duplicate_of = doc
    with pytest.raises(IntegrityError):
        doc.save()


def test_effective_doc_type() -> None:
    assert DocumentFactory(doc_type=DocType.RECEIPT).effective_doc_type == DocType.RECEIPT
    auto = DocumentFactory(doc_type=DocType.AUTO, detected_doc_type=DocType.INVOICE)
    assert auto.effective_doc_type == DocType.INVOICE


def test_soft_delete_hides_but_keeps_row() -> None:
    user = UserFactory()
    doc = DocumentFactory()
    doc.soft_delete(actor=user)

    assert not Document.objects.filter(pk=doc.pk).exists()
    kept = Document.all_objects.get(pk=doc.pk)
    assert kept.is_deleted
    assert kept.deleted_by == user


def test_hard_delete_is_forbidden() -> None:
    doc = DocumentFactory()
    with pytest.raises(HardDeleteForbidden):
        doc.delete()
    with pytest.raises(HardDeleteForbidden):
        Document.objects.all().delete()
    assert Document.all_objects.filter(pk=doc.pk).exists()


def test_latest_result_is_highest_version() -> None:
    doc = DocumentFactory()
    ExtractionResultFactory(document=doc, version=1)
    v2 = ExtractionResultFactory(document=doc, version=2)
    assert doc.latest_result() == v2


# ------------------------------------------------------------- Extraction


def test_result_version_unique_per_document() -> None:
    result = ExtractionResultFactory(version=1)
    with pytest.raises(IntegrityError):
        ExtractionResultFactory(document=result.document, version=1)


def test_result_version_must_be_positive() -> None:
    with pytest.raises(IntegrityError):
        ExtractionResultFactory(version=0)


def test_field_key_unique_per_result() -> None:
    field = ExtractedFieldFactory(key="total")
    with pytest.raises(IntegrityError):
        ExtractedFieldFactory(result=field.result, key="total")


def test_line_no_unique_per_result() -> None:
    line = ExtractedLineFactory(line_no=1)
    with pytest.raises(IntegrityError):
        ExtractedLineFactory(result=line.result, line_no=1)


@pytest.mark.parametrize("column", ["debit", "credit"])
def test_debit_and_credit_cannot_be_negative(column: str) -> None:
    with pytest.raises(IntegrityError):
        ExtractedLineFactory(**{column: Decimal("-1.00")})


def test_money_round_trips_as_decimal() -> None:
    line = ExtractedLineFactory(amount=Decimal("-1234.56"), balance=Decimal("0.10"))
    line.refresh_from_db()
    assert line.amount == Decimal("-1234.56")
    assert isinstance(line.balance, Decimal)


def test_soft_deleted_line_hidden_from_result() -> None:
    line = ExtractedLineFactory()
    other = ExtractedLineFactory(result=line.result)
    line.soft_delete()
    assert list(line.result.lines.all()) == [other]
    assert ExtractedLine.all_objects.filter(pk=line.pk).exists()


def test_invalid_severity_rejected() -> None:
    with pytest.raises(IntegrityError):
        ValidationIssueFactory(severity="fatal")


# ----------------------------------------------------------------- Audit log


def test_audit_entry_cannot_be_updated_or_deleted_via_orm() -> None:
    entry = AuditLogFactory()
    entry.metadata = {"changed": True}
    with pytest.raises(AuditLogImmutable):
        entry.save()
    with pytest.raises(AuditLogImmutable):
        entry.delete()
    with pytest.raises(AuditLogImmutable):
        AuditLog.objects.filter(pk=entry.pk).update(action=AuditAction.APPROVE)
    with pytest.raises(AuditLogImmutable):
        AuditLog.objects.all().delete()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE document_intake_auditlog SET action = 'approve'",
        "DELETE FROM document_intake_auditlog",
    ],
)
def test_audit_trigger_blocks_raw_sql(sql: str) -> None:
    AuditLogFactory()
    with (
        pytest.raises(IntegrityError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)
    assert AuditLog.objects.count() == 1


def test_record_stores_actor_and_metadata() -> None:
    doc = DocumentFactory()
    entry = audit.record(AuditAction.UPLOAD, doc, doc.uploaded_by, size_bytes=10)
    assert entry.actor == doc.uploaded_by
    assert entry.metadata == {"size_bytes": 10}


def test_record_treats_anonymous_as_system() -> None:
    entry = audit.record(AuditAction.EXTRACT, DocumentFactory(), AnonymousUser())
    assert entry.actor is None


# --------------------------------------------------------------------- Admin


ADMIN_MODELS = [
    "document",
    "extractionresult",
    "extractedfield",
    "extractedline",
    "validationissue",
    "auditlog",
]


@pytest.mark.parametrize("model", ADMIN_MODELS)
def test_admin_changelist_and_detail_render(admin_client, model: str) -> None:
    line = ExtractedLineFactory()
    ExtractedFieldFactory(result=line.result)
    ValidationIssueFactory(result=line.result)
    AuditLogFactory(document=line.result.document)

    changelist = admin_client.get(reverse(f"admin:document_intake_{model}_changelist"))
    assert changelist.status_code == 200
    add = admin_client.get(reverse(f"admin:document_intake_{model}_add"))
    assert add.status_code == 403


def test_admin_document_detail_renders_soft_deleted(admin_client) -> None:
    doc = DocumentFactory()
    ExtractionResultFactory(document=doc)
    doc.soft_delete()
    url = reverse("admin:document_intake_document_change", args=[doc.pk])
    assert admin_client.get(url).status_code == 200


# ---------------------------------------------------------------- Migrations


def test_no_missing_migrations() -> None:
    call_command("makemigrations", "document_intake", "--check", "--dry-run", verbosity=0)
