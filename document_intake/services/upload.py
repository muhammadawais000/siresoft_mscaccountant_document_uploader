"""Upload intake: validate the file, store it privately, record it, queue extraction."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import magic
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from PIL import Image, UnidentifiedImageError

from document_intake.conf import intake_settings
from document_intake.exceptions import UploadRejected
from document_intake.models import AuditAction, DocType, Document, DocumentStatus

from . import audit

logger = logging.getLogger(__name__)

_MAGIC_SNIFF_BYTES = 8192
_HASH_CHUNK = 1024 * 1024
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ValidatedFile:
    """Facts about an uploaded file, all derived server-side."""

    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str


def sanitize_filename(name: str) -> str:
    """Return a display-safe filename: no directories, control characters or overlong names.

    The result is only ever shown to people; storage paths never use it.
    """
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS.sub("", name).strip().lstrip(".")
    if not name:
        return "document"
    if len(name) > 255:
        suffix = PurePosixPath(name).suffix[:16]
        name = name[: 255 - len(suffix)] + suffix
    return name


def _detect_mime(upload: UploadedFile[bytes]) -> str:
    upload.seek(0)
    head = upload.read(_MAGIC_SNIFF_BYTES)
    upload.seek(0)
    return str(magic.from_buffer(head, mime=True))


def _sha256(upload: UploadedFile[bytes]) -> str:
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in upload.chunks(_HASH_CHUNK):
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


def _check_image(upload: UploadedFile[bytes]) -> None:
    """Reject corrupt images and decompression bombs before anything else touches them."""
    upload.seek(0)
    try:
        with Image.open(upload) as img:
            width, height = img.size
            if width * height > intake_settings.MAX_IMAGE_PIXELS:
                raise UploadRejected("The image dimensions are too large.")
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise UploadRejected("The image file is damaged or unreadable.") from exc
    finally:
        upload.seek(0)


def validate_upload(upload: UploadedFile[bytes]) -> ValidatedFile:
    """Check extension, real content type (magic bytes) and size; compute SHA-256.

    Raises:
        UploadRejected: with a message safe to show to the uploader.
    """
    filename = sanitize_filename(upload.name or "")
    allowed: dict[str, tuple[str, ...]] = intake_settings.ALLOWED_MIME_TYPES
    extension = PurePosixPath(filename).suffix.lower()
    allowed_exts = sorted({ext for exts in allowed.values() for ext in exts})
    if extension not in allowed_exts:
        raise UploadRejected(f"File type not allowed. Allowed: {', '.join(allowed_exts)}.")

    size = upload.size or 0
    if size == 0:
        raise UploadRejected("The file is empty.")
    max_bytes: int = intake_settings.MAX_UPLOAD_BYTES
    if size > max_bytes:
        raise UploadRejected(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")

    mime = _detect_mime(upload)
    if mime not in allowed:
        raise UploadRejected("The file content is not a supported document type.")
    if extension not in allowed[mime]:
        raise UploadRejected("The file extension does not match its content.")
    if mime.startswith("image/"):
        _check_image(upload)

    return ValidatedFile(
        original_filename=filename,
        mime_type=mime,
        size_bytes=size,
        sha256=_sha256(upload),
    )


def find_duplicate(
    sha256: str, *, uploaded_by: Any, owner_reference: uuid.UUID | None
) -> Document | None:
    """Earliest live document with the same content for the same owner.

    The owner is the portal client (``owner_reference``) when given, else the uploader.
    """
    if not intake_settings.FEATURE_DUPLICATE_DETECTION:
        return None
    qs = Document.objects.filter(sha256=sha256)
    if owner_reference is not None:
        qs = qs.filter(owner_reference=owner_reference)
    else:
        qs = qs.filter(owner_reference__isnull=True, uploaded_by=uploaded_by)
    return qs.order_by("created_at").first()


def create_document(
    *,
    upload: UploadedFile[bytes],
    uploaded_by: Any,
    doc_type: str = DocType.AUTO,
    owner_reference: uuid.UUID | None = None,
    request_reference: str = "",
    enqueue: bool = True,
) -> Document:
    """Validate and store an upload, write the audit entry, and queue extraction.

    Exact duplicates (same SHA-256 for the same owner) are kept and linked via
    ``duplicate_of``. With SKIP_EXTRACTION_FOR_DUPLICATES (default) they go straight to
    status ``duplicate`` and are not sent to the model. ``enqueue=False`` lets a caller run
    extraction itself (e.g. the demo seeder).
    """
    validated = validate_upload(upload)
    duplicate = find_duplicate(
        validated.sha256, uploaded_by=uploaded_by, owner_reference=owner_reference
    )

    document = Document(
        uploaded_by=uploaded_by,
        owner_reference=owner_reference,
        request_reference=request_reference,
        original_filename=validated.original_filename,
        mime_type=validated.mime_type,
        size_bytes=validated.size_bytes,
        sha256=validated.sha256,
        doc_type=doc_type,
        duplicate_of=duplicate,
    )
    skip_extraction = duplicate is not None and intake_settings.SKIP_EXTRACTION_FOR_DUPLICATES
    if skip_extraction:
        document.status = DocumentStatus.DUPLICATE
    # The storage name is built from the document id (see document_upload_to).
    document.file.save(validated.original_filename, upload, save=False)
    try:
        with transaction.atomic():
            document.save()
            audit.record(
                AuditAction.UPLOAD,
                document,
                uploaded_by,
                size_bytes=validated.size_bytes,
                mime_type=validated.mime_type,
                duplicate_of=str(duplicate.id) if duplicate else None,
            )
            if enqueue and not skip_extraction:
                transaction.on_commit(lambda: _enqueue_extraction(document.id))
    except Exception:
        # Do not leave an orphaned file behind if the database write failed.
        document.file.delete(save=False)
        raise

    logger.info(
        "document uploaded",
        extra={"document_id": str(document.id), "duplicate": duplicate is not None},
    )
    return document


def _enqueue_extraction(document_id: uuid.UUID) -> None:
    from document_intake.tasks import extract_document

    extract_document.delay(str(document_id))
