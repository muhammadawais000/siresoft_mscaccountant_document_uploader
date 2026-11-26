"""Attach and remove purchase receipts on credit card statements.

A receipt is a file on one purchase line. Anyone who may see the document may attach one
(clients keep the receipts); every attach, view and removal is in the audit log. Files are
validated like uploads and stored privately.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from document_intake.exceptions import InvalidTransition, UploadRejected
from document_intake.models import (
    AuditAction,
    DocType,
    Document,
    DocumentStatus,
    ExtractionResult,
    TransactionReceipt,
)

from . import audit
from .upload import validate_upload

logger = logging.getLogger(__name__)

IN_PROGRESS = (DocumentStatus.UPLOADED, DocumentStatus.PROCESSING)


def attach(
    document: Document, line_id: Any, upload: UploadedFile[bytes], actor: Any
) -> TransactionReceipt:
    """Store ``upload`` as a receipt for one purchase on the latest result.

    Raises:
        UploadRejected: bad file, or the same file already on this purchase.
        InvalidTransition: not a card statement, still processing, or not a purchase.
        ExtractedLine.DoesNotExist: no such line on the latest result.
    """
    validated = validate_upload(upload)
    with transaction.atomic():
        locked: Document = Document.objects.select_for_update().get(pk=document.pk)
        if locked.effective_doc_type != DocType.CREDIT_CARD_STATEMENT:
            raise InvalidTransition("Receipts can be attached to credit card statements only.")
        if locked.status in IN_PROGRESS:
            raise InvalidTransition("Wait until the statement has been extracted.")
        result = locked.latest_result()
        if result is None:
            raise InvalidTransition("There is no extraction result yet.")
        line = result.lines.get(pk=line_id)
        if line.debit is None:
            raise InvalidTransition("Receipts can be attached to purchases only.")
        if line.receipts.filter(sha256=validated.sha256).exists():
            raise UploadRejected("This receipt is already attached to this purchase.")
        receipt = TransactionReceipt(
            line=line,
            uploaded_by=actor,
            original_filename=validated.original_filename,
            mime_type=validated.mime_type,
            size_bytes=validated.size_bytes,
            sha256=validated.sha256,
        )
        receipt.file.save(validated.original_filename, upload, save=False)
        try:
            receipt.save()
            audit.record(
                AuditAction.UPLOAD,
                locked,
                actor,
                target="receipt",
                receipt=str(receipt.id),
                line_no=line.line_no,
                version=result.version,
                mime_type=validated.mime_type,
            )
        except Exception:
            receipt.file.delete(save=False)
            raise
    logger.info("receipt attached", extra={"document_id": str(document.id)})
    return receipt


def get(document: Document, receipt_id: Any) -> TransactionReceipt:
    """A live receipt of this document (any version). Raises DoesNotExist."""
    receipt: TransactionReceipt = TransactionReceipt.objects.select_related("line").get(
        pk=receipt_id, line__result__document=document
    )
    return receipt


def remove(document: Document, receipt_id: Any, actor: Any) -> None:
    """Soft delete: the file and the audit entry stay."""
    with transaction.atomic():
        Document.objects.select_for_update().get(pk=document.pk)
        receipt = get(document, receipt_id)
        receipt.soft_delete(actor if getattr(actor, "is_authenticated", False) else None)
        audit.record(
            AuditAction.DELETE,
            document,
            actor,
            target="receipt",
            receipt=str(receipt.id),
            line_no=receipt.line.line_no,
        )


def carry_over(previous: ExtractionResult | None, result: ExtractionResult) -> int:
    """Move receipts from the previous version's purchases to the same purchases in ``result``.

    Matched by date, amount and card. Returns how many could not be matched; those stay on
    the old version (nothing is lost) and are reported in the log.
    """
    if previous is None:
        return 0
    receipts = list(TransactionReceipt.objects.filter(line__result=previous).select_related("line"))
    if not receipts:
        return 0
    lines = list(result.lines.filter(debit__isnull=False).order_by("line_no"))
    unmatched = 0
    claimed: dict[Any, Any] = {}  # old line -> new line, so two receipts on one line stay together
    taken: set[Any] = set()
    for receipt in receipts:
        old = receipt.line
        new = claimed.get(old.pk)
        if new is None:
            new = next(
                (
                    line
                    for line in lines
                    if line.pk not in taken
                    and line.debit == old.debit
                    and line.date == old.date
                    and line.card_last4 == old.card_last4
                ),
                None,
            )
        if new is None:
            unmatched += 1
            continue
        claimed[old.pk] = new
        taken.add(new.pk)
        receipt.line = new
        receipt.save(update_fields=["line", "updated_at"])
    if unmatched:
        logger.warning(
            "receipts not carried over",
            extra={"document_id": str(result.document_id), "count": unmatched},
        )
    return unmatched
