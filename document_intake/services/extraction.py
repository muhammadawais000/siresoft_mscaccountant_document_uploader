"""Extraction pipeline: route the document, call the provider, store a new result version.

Runs in the Celery worker. Slow work (PDF parsing, model calls) happens outside database
transactions; only the short status change and the final write are transactional.
"""

from __future__ import annotations

import io
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Max

from document_intake.conf import intake_settings
from document_intake.exceptions import DocumentTooLarge, ExtractionError, InvalidTransition
from document_intake.extraction import parsing, prompts, schemas, sources
from document_intake.models import (
    AuditAction,
    DocType,
    Document,
    DocumentStatus,
    ExtractedField,
    ExtractedLine,
    ExtractionMethod,
    ExtractionResult,
)
from document_intake.providers.base import ExtractionProvider, ModelRequest, get_provider
from document_intake.signals import document_extracted

from . import audit, lifecycle, validation

logger = logging.getLogger(__name__)

RUNNABLE_STATUSES = (DocumentStatus.UPLOADED, DocumentStatus.PROCESSING)


@dataclass(frozen=True)
class PreparedInput:
    """What the model will see, decided by routing."""

    method: ExtractionMethod
    page_count: int
    text: str = ""
    images: tuple[bytes, ...] = ()

    @property
    def vision(self) -> bool:
        return self.method == ExtractionMethod.VISION


def prepare_input(data: bytes, mime_type: str) -> PreparedInput:
    """Route a document.

    PDF with a usable text layer -> pdfplumber text (no vision call).
    Scanned PDF or image -> page images for the vision model.
    """
    if mime_type == "application/pdf":
        pdf = sources.read_pdf_text(io.BytesIO(data))
        if pdf.has_text_layer():
            text = pdf.text
            if len(text) > intake_settings.MAX_TEXT_CHARS:
                raise DocumentTooLarge(
                    "The PDF text is longer than the configured model context allows."
                )
            return PreparedInput(ExtractionMethod.PDF_TEXT, pdf.page_count, text=text)
        images = sources.render_pdf_pages(data)
        return PreparedInput(ExtractionMethod.VISION, pdf.page_count, images=tuple(images))
    if mime_type.startswith("image/"):
        return PreparedInput(ExtractionMethod.VISION, 1, images=tuple(sources.load_image(data)))
    raise ExtractionError(f"No extraction route for content type {mime_type}.")


def _request(prepared: PreparedInput, task: str, schema: dict[str, Any]) -> ModelRequest:
    system = prompts.system_prompt(str(intake_settings.DEFAULT_CURRENCY))
    if prepared.vision:
        user = prompts.image_user_prompt(task, len(prepared.images))
        return ModelRequest(system, user, schema, images=prepared.images, vision=True)
    return ModelRequest(system, prompts.text_user_prompt(task, prepared.text), schema)


def classify(provider: ExtractionProvider, prepared: PreparedInput) -> str:
    """Ask the model which of the four document types this is."""
    if not intake_settings.FEATURE_AUTO_CLASSIFY:
        raise ExtractionError("Auto-detect is disabled; choose a document type.")
    response = provider.complete_json(
        _request(prepared, prompts.CLASSIFY_PROMPT, schemas.CLASSIFICATION_SCHEMA)
    )
    return str(response.data["doc_type"])


def _start(document_id: uuid.UUID | str) -> Document | None:
    """Claim the document for processing; None if it is not in a runnable state."""
    with transaction.atomic():
        document: Document = Document.objects.select_for_update().get(pk=document_id)
        if document.status not in RUNNABLE_STATUSES:
            logger.info(
                "extraction skipped",
                extra={"document_id": str(document.id), "status": document.status},
            )
            return None
        if document.status == DocumentStatus.PROCESSING:
            return document  # resuming after a worker crash or a retry
        return lifecycle.transition(document, DocumentStatus.PROCESSING, error_message="")


def run_extraction(
    document_id: uuid.UUID | str, provider: ExtractionProvider | None = None
) -> ExtractionResult | None:
    """Extract one document and store result version n+1. Idempotent.

    Returns None when the document is not in a runnable state (already done, approved,
    rejected...), so a re-delivered task is harmless.
    """
    document = _start(document_id)
    if document is None:
        return None
    provider = provider or get_provider()
    started = time.monotonic()

    with document.file.open("rb") as fp:
        data = fp.read()
    prepared = prepare_input(data, document.mime_type)

    doc_type = document.doc_type
    detected = ""
    if doc_type == DocType.AUTO:
        detected = doc_type = classify(provider, prepared)

    task = prompts.TASK_PROMPTS[doc_type]
    response = provider.complete_json(_request(prepared, task, schemas.SCHEMA_FOR_TYPE[doc_type]))
    duration_ms = int((time.monotonic() - started) * 1000)

    return _store(
        document,
        doc_type,
        detected,
        prepared,
        provider,
        response.data,
        response.model_name,
        duration_ms,
    )


def _store(
    document: Document,
    doc_type: str,
    detected: str,
    prepared: PreparedInput,
    provider: ExtractionProvider,
    data: dict[str, Any],
    model_name: str,
    duration_ms: int,
) -> ExtractionResult:
    currency = str(intake_settings.DEFAULT_CURRENCY)
    field_rows = parsing.build_fields(doc_type, data, currency)
    line_rows = parsing.build_lines(doc_type, data)

    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk)
        last = document.results.aggregate(v=Max("version"))["v"] or 0
        result = ExtractionResult.objects.create(
            document=document,
            version=last + 1,
            raw_text=prepared.text,
            structured_data=data,
            extraction_method=prepared.method,
            provider=provider.name,
            model_name=model_name,
            prompt_version=prompts.PROMPT_VERSION,
            duration_ms=duration_ms,
        )
        ExtractedField.objects.bulk_create(
            ExtractedField(
                result=result,
                key=row.key,
                label=row.label,
                position=row.position,
                value=row.value.value,
                normalized_value=row.value.normalized_value,
            )
            for row in field_rows
        )
        ExtractedLine.objects.bulk_create(
            ExtractedLine(
                result=result,
                line_no=row.line_no,
                date=row.date,
                description=row.description,
                debit=row.debit,
                credit=row.credit,
                amount=row.amount,
                balance=row.balance,
                tax=row.tax,
                raw=row.raw,
            )
            for row in line_rows
        )

        document.extraction_method = prepared.method
        document.page_count = prepared.page_count
        if detected:
            document.detected_doc_type = detected
        document.save(
            update_fields=["extraction_method", "page_count", "detected_doc_type", "updated_at"]
        )
        issues = validation.validate_result(result)
        audit.record(
            AuditAction.EXTRACT,
            document,
            None,
            outcome="success",
            version=result.version,
            method=prepared.method,
            model=model_name,
            prompt_version=prompts.PROMPT_VERSION,
            duration_ms=duration_ms,
            fields=len(field_rows),
            lines=len(line_rows),
            issues=len(issues),
        )
        # Never auto-approved: a person reviews every extraction.
        document = lifecycle.transition(document, DocumentStatus.NEEDS_REVIEW)
        result.document = document
        transaction.on_commit(
            lambda: document_extracted.send(sender=Document, document=document, result=result)
        )

    logger.info(
        "extraction stored",
        extra={
            "document_id": str(document.id),
            "version": result.version,
            "method": prepared.method,
            "duration_ms": duration_ms,
        },
    )
    return result


def mark_failed(document_id: uuid.UUID | str, reason: str) -> None:
    """Dead-letter state: the document needs an operator (fix config, then reprocess)."""
    document = Document.objects.get(pk=document_id)
    try:
        with transaction.atomic():
            lifecycle.transition(
                document, DocumentStatus.FAILED, error_message=reason, reason=reason[:500]
            )
            audit.record(AuditAction.EXTRACT, document, None, outcome="failed")
    except InvalidTransition:
        # Someone already moved it on (e.g. rejected while we worked); keep their decision.
        logger.warning(
            "could not mark failed",
            extra={"document_id": str(document_id), "status": document.status},
        )
        return
    logger.warning("extraction failed", extra={"document_id": str(document_id)})
