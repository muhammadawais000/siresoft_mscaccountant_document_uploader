"""Step 3: upload validation, storage and duplicate detection (service layer)."""

import hashlib
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

from document_intake.exceptions import UploadRejected
from document_intake.models import AuditAction, AuditLog, DocType, Document
from document_intake.services.upload import create_document, sanitize_filename, validate_upload
from document_intake.tests.conftest import make_image, make_pdf
from document_intake.tests.factories import DocumentFactory

# ------------------------------------------------------------ sanitize_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("statement.pdf", "statement.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\me\\receipt.jpg", "receipt.jpg"),
        ("bad\x00name\x1f.pdf", "badname.pdf"),
        ("  .hidden.pdf ", "hidden.pdf"),
        ("", "document"),
        ("../", "document"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_but_keeps_extension() -> None:
    result = sanitize_filename("a" * 400 + ".pdf")
    assert len(result) == 255
    assert result.endswith(".pdf")


# ---------------------------------------------------------------- validate


def test_valid_pdf(make_upload, pdf_bytes) -> None:
    validated = validate_upload(make_upload("Sept.PDF", pdf_bytes))
    assert validated.mime_type == "application/pdf"
    assert validated.size_bytes == len(pdf_bytes)
    assert validated.sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert validated.original_filename == "Sept.PDF"


@pytest.mark.parametrize(
    ("name", "fmt", "mime"),
    [
        ("r.png", "PNG", "image/png"),
        ("r.jpg", "JPEG", "image/jpeg"),
        ("r.jpeg", "JPEG", "image/jpeg"),
    ],
)
def test_valid_images(make_upload, name: str, fmt: str, mime: str) -> None:
    assert validate_upload(make_upload(name, make_image(fmt))).mime_type == mime


def test_rejects_disallowed_extension(make_upload, pdf_bytes) -> None:
    with pytest.raises(UploadRejected, match="File type not allowed"):
        validate_upload(make_upload("statement.exe", pdf_bytes))


def test_rejects_spoofed_extension(make_upload) -> None:
    # An executable renamed to .pdf: the extension is allowed but the content is not.
    with pytest.raises(UploadRejected, match="not a supported document type"):
        validate_upload(make_upload("statement.pdf", b"MZ\x90\x00" + b"\x00" * 200))


def test_rejects_extension_content_mismatch(make_upload) -> None:
    # A real PNG named .pdf.
    with pytest.raises(UploadRejected, match="does not match"):
        validate_upload(make_upload("statement.pdf", make_image("PNG")))


def test_rejects_html_disguised_as_pdf(make_upload) -> None:
    with pytest.raises(UploadRejected):
        validate_upload(make_upload("x.pdf", b"<html><script>alert(1)</script></html>"))


def test_rejects_empty_file(make_upload) -> None:
    with pytest.raises(UploadRejected, match="empty"):
        validate_upload(make_upload("empty.pdf", b""))


def test_rejects_oversized_file(make_upload, pdf_bytes) -> None:
    with (
        override_settings(DOCUMENT_INTAKE={"MAX_UPLOAD_BYTES": len(pdf_bytes) - 1}),
        pytest.raises(UploadRejected, match="larger than"),
    ):
        validate_upload(make_upload("big.pdf", pdf_bytes))


def test_rejects_truncated_image(make_upload) -> None:
    png = make_image("PNG")
    with pytest.raises(UploadRejected, match="damaged"):
        validate_upload(make_upload("cut.png", png[: len(png) // 2]))


def test_rejects_huge_image_dimensions(make_upload) -> None:
    with (
        override_settings(DOCUMENT_INTAKE={"MAX_IMAGE_PIXELS": 100}),
        pytest.raises(UploadRejected, match="too large"),
    ):
        validate_upload(make_upload("big.png", make_image("PNG", (20, 20))))


# ------------------------------------------------------------------ create


@pytest.mark.django_db
def test_create_document_stores_file_and_audits(make_upload, pdf_bytes, user) -> None:
    doc = create_document(
        upload=make_upload("sept.pdf", pdf_bytes), uploaded_by=user, doc_type=DocType.BANK_STATEMENT
    )

    assert doc.status == "uploaded"
    assert doc.doc_type == DocType.BANK_STATEMENT
    assert doc.duplicate_of is None
    with doc.file.open("rb") as stored:
        assert stored.read() == pdf_bytes
    entry = AuditLog.objects.get(document=doc)
    assert entry.action == AuditAction.UPLOAD
    assert entry.actor == user
    assert entry.metadata["mime_type"] == "application/pdf"


@pytest.mark.django_db
def test_rejected_upload_creates_nothing(make_upload, user) -> None:
    with pytest.raises(UploadRejected):
        create_document(upload=make_upload("x.pdf", b"not a pdf at all"), uploaded_by=user)
    assert Document.all_objects.count() == 0


@pytest.mark.django_db
def test_queues_extraction_after_commit(
    make_upload, pdf_bytes, user, django_capture_on_commit_callbacks
) -> None:
    with patch("document_intake.tasks.extract_document.delay") as delay:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            doc = create_document(upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user)
        delay.assert_not_called()  # never queued before the row is committed
        for callback in callbacks:
            callback()
    delay.assert_called_once_with(str(doc.id))


def _stored_files() -> set[str]:
    root = Path(Document._meta.get_field("file").storage.location)
    return {str(p) for p in root.rglob("*") if p.is_file()}


@pytest.mark.django_db
def test_failed_db_write_removes_stored_file(make_upload, pdf_bytes, user) -> None:
    before = _stored_files()
    with (
        patch("document_intake.services.audit.record", side_effect=RuntimeError("db down")),
        pytest.raises(RuntimeError),
    ):
        create_document(upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user)
    assert Document.all_objects.count() == 0
    assert _stored_files() == before


# -------------------------------------------------------------- duplicates


@pytest.mark.django_db
def test_same_user_same_content_is_flagged(make_upload, pdf_bytes, user) -> None:
    first = create_document(upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user)
    second = create_document(upload=make_upload("b.pdf", pdf_bytes), uploaded_by=user)
    assert second.duplicate_of == first
    assert AuditLog.objects.get(document=second).metadata["duplicate_of"] == str(first.id)


@pytest.mark.django_db
def test_different_user_same_content_not_flagged(make_upload, pdf_bytes, user, other_user) -> None:
    create_document(upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user)
    second = create_document(upload=make_upload("a.pdf", pdf_bytes), uploaded_by=other_user)
    assert second.duplicate_of is None


@pytest.mark.django_db
def test_owner_reference_scopes_duplicates(make_upload, pdf_bytes, user, other_user) -> None:
    client = uuid.uuid4()
    first = create_document(
        upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user, owner_reference=client
    )
    # A colleague uploads the same statement for the same client: still a duplicate.
    same_client = create_document(
        upload=make_upload("a.pdf", pdf_bytes), uploaded_by=other_user, owner_reference=client
    )
    other_client = create_document(
        upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user, owner_reference=uuid.uuid4()
    )
    assert same_client.duplicate_of == first
    assert other_client.duplicate_of is None


@pytest.mark.django_db
def test_soft_deleted_document_is_not_a_duplicate_source(make_upload, pdf_bytes, user) -> None:
    first = create_document(upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user)
    first.soft_delete(actor=user)
    second = create_document(upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user)
    assert second.duplicate_of is None


@pytest.mark.django_db
def test_duplicate_detection_can_be_disabled(make_upload, pdf_bytes, user) -> None:
    DocumentFactory(uploaded_by=user, content=pdf_bytes)
    with override_settings(DOCUMENT_INTAKE={"FEATURE_DUPLICATE_DETECTION": False}):
        doc = create_document(upload=make_upload("a.pdf", pdf_bytes), uploaded_by=user)
    assert doc.duplicate_of is None


def test_make_pdf_has_text_layer() -> None:
    assert b"%PDF" in make_pdf()[:8]
