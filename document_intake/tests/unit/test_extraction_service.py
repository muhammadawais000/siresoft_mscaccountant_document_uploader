"""Step 4: extraction pipeline, Celery task behaviour and failure handling."""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile

from document_intake import tasks
from document_intake.exceptions import (
    DocumentTooLarge,
    ExtractionError,
    MalformedModelOutput,
    ProviderUnavailable,
)
from document_intake.extraction import prompts, schemas
from document_intake.models import (
    AuditAction,
    AuditLog,
    DocType,
    DocumentStatus,
    ExtractionMethod,
    FieldStatus,
)
from document_intake.services.extraction import mark_failed, run_extraction
from document_intake.signals import document_extracted
from document_intake.tests.conftest import make_image
from document_intake.tests.factories import DocumentFactory
from document_intake.tests.fakes import BANK_STATEMENT, RECEIPT, FakeProvider
from document_intake.tests.unit.test_sources import scanned_pdf, statement_pdf

pytestmark = pytest.mark.django_db


def text_pdf_document(**kwargs):
    return DocumentFactory(content=statement_pdf(), doc_type=DocType.BANK_STATEMENT, **kwargs)


def test_text_pdf_path(django_capture_on_commit_callbacks) -> None:
    doc = text_pdf_document()
    provider = FakeProvider(BANK_STATEMENT)
    received = []
    document_extracted.connect(lambda **kw: received.append(kw), weak=False, dispatch_uid="t1")
    try:
        with django_capture_on_commit_callbacks(execute=True):
            result = run_extraction(doc.id, provider)
    finally:
        document_extracted.disconnect(dispatch_uid="t1")

    doc.refresh_from_db()
    assert doc.status == DocumentStatus.NEEDS_REVIEW
    assert doc.extraction_method == ExtractionMethod.PDF_TEXT
    assert doc.page_count == 1
    assert result.version == 1
    assert result.provider == "fake"
    assert result.model_name == "fake-text"
    assert result.prompt_version == prompts.PROMPT_VERSION
    assert "Transaction line 0" in result.raw_text
    assert result.structured_data == BANK_STATEMENT

    (request,) = provider.requests  # no classification call, no images
    assert not request.vision
    assert request.schema is schemas.STATEMENT_SCHEMA
    assert "Transaction line 0" in request.user

    fields = {f.key: f for f in result.fields.all()}
    assert fields["closing_balance"].normalized_value == "4200.00"
    lines = list(result.lines.all())
    assert [line.amount for line in lines] == [Decimal("-1500.00"), Decimal("700.00")]

    entry = AuditLog.objects.get(document=doc, action=AuditAction.EXTRACT)
    assert entry.actor is None
    assert entry.metadata["outcome"] == "success"
    assert received[0]["result"] == result


def test_image_goes_to_vision_model() -> None:
    doc = DocumentFactory(
        content=make_image("JPEG"),
        original_filename="r.jpg",
        mime_type="image/jpeg",
        doc_type=DocType.RECEIPT,
    )
    provider = FakeProvider(RECEIPT)
    result = run_extraction(doc.id, provider)

    (request,) = provider.requests
    assert request.vision
    assert len(request.images) == 1
    assert result.extraction_method == ExtractionMethod.VISION
    assert result.raw_text == ""
    assert result.model_name == "fake-vision"
    assert result.fields.get(key="currency").status == FieldStatus.WARNING


def test_scanned_pdf_goes_to_vision_model() -> None:
    doc = DocumentFactory(content=scanned_pdf(2), doc_type=DocType.RECEIPT)
    provider = FakeProvider(RECEIPT)
    run_extraction(doc.id, provider)
    doc.refresh_from_db()
    assert doc.extraction_method == ExtractionMethod.VISION
    assert doc.page_count == 2
    assert len(provider.requests[0].images) == 2


def test_auto_detect_classifies_first() -> None:
    doc = DocumentFactory(content=statement_pdf(), doc_type=DocType.AUTO)
    provider = FakeProvider(BANK_STATEMENT, doc_type=DocType.BANK_STATEMENT)
    run_extraction(doc.id, provider)

    doc.refresh_from_db()
    assert doc.detected_doc_type == DocType.BANK_STATEMENT
    assert doc.effective_doc_type == DocType.BANK_STATEMENT
    classify, extract = provider.requests
    assert classify.schema is schemas.CLASSIFICATION_SCHEMA
    assert extract.schema is schemas.STATEMENT_SCHEMA


def test_auto_detect_disabled(settings) -> None:
    settings.DOCUMENT_INTAKE = {**settings.DOCUMENT_INTAKE, "FEATURE_AUTO_CLASSIFY": False}
    doc = DocumentFactory(content=statement_pdf(), doc_type=DocType.AUTO)
    with pytest.raises(ExtractionError, match="Auto-detect is disabled"):
        run_extraction(doc.id, FakeProvider())


def test_rerun_creates_new_version_and_keeps_history() -> None:
    doc = text_pdf_document()
    first = run_extraction(doc.id, FakeProvider(BANK_STATEMENT))
    doc.status = DocumentStatus.UPLOADED
    doc.save()
    second = run_extraction(doc.id, FakeProvider({**BANK_STATEMENT, "transactions": []}))

    assert (first.version, second.version) == (1, 2)
    assert doc.latest_result() == second
    assert first.lines.count() == 2
    assert second.lines.count() == 0


@pytest.mark.parametrize(
    "status",
    [
        DocumentStatus.NEEDS_REVIEW,
        DocumentStatus.APPROVED,
        DocumentStatus.REJECTED,
        DocumentStatus.DUPLICATE,
        DocumentStatus.FAILED,
    ],
)
def test_not_runnable_states_are_skipped(status) -> None:
    doc = text_pdf_document(status=status)
    provider = FakeProvider(BANK_STATEMENT)
    assert run_extraction(doc.id, provider) is None
    assert provider.requests == []
    doc.refresh_from_db()
    assert doc.status == status


def test_processing_state_can_resume_after_crash() -> None:
    doc = text_pdf_document(status=DocumentStatus.PROCESSING)
    assert run_extraction(doc.id, FakeProvider(BANK_STATEMENT)).version == 1


def test_mark_failed() -> None:
    doc = text_pdf_document(status=DocumentStatus.PROCESSING)
    mark_failed(doc.id, "Ollama timed out.")
    doc.refresh_from_db()
    assert doc.status == DocumentStatus.FAILED
    assert doc.error_message == "Ollama timed out."
    entry = AuditLog.objects.get(document=doc)
    assert entry.metadata == {"outcome": "failed", "reason": "Ollama timed out."}


# -------------------------------------------------------------------- task


def run_task(doc, provider) -> None:
    with patch("document_intake.services.extraction.get_provider", return_value=provider):
        tasks.extract_document.delay(str(doc.id))
    doc.refresh_from_db()


def test_task_success() -> None:
    doc = text_pdf_document()
    run_task(doc, FakeProvider(BANK_STATEMENT))
    assert doc.status == DocumentStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ProviderUnavailable("Ollama timed out."), "Ollama timed out."),
        (MalformedModelOutput("The model output is not valid JSON."), "not valid JSON"),
        (ExtractionError("Model 'x' is not available on Ollama."), "not available"),
        (DocumentTooLarge("too many pages"), "too many pages"),
        (RuntimeError("secret amount 1234.56"), "Unexpected error"),
    ],
)
def test_task_failures_end_in_failed_state(error, message) -> None:
    doc = text_pdf_document()
    run_task(doc, FakeProvider(error=error))
    assert doc.status == DocumentStatus.FAILED
    assert message in doc.error_message
    assert "1234.56" not in doc.error_message  # unexpected errors never leak details


def test_task_retries_provider_outage_when_not_eager() -> None:
    doc = text_pdf_document()
    task = tasks.extract_document
    with (
        patch(
            "document_intake.services.extraction.get_provider",
            return_value=FakeProvider(error=ProviderUnavailable("down")),
        ),
        patch.object(task, "retry", side_effect=RuntimeError("retry scheduled")) as retry,
    ):
        task.push_request(retries=0, is_eager=False)
        try:
            with pytest.raises(RuntimeError, match="retry scheduled"):
                task.run(str(doc.id))
        finally:
            task.pop_request()
    assert retry.call_args.kwargs["countdown"] == 30
    doc.refresh_from_db()
    assert doc.status == DocumentStatus.PROCESSING  # will resume on retry


def test_task_gives_up_after_max_retries() -> None:
    doc = text_pdf_document()
    task = tasks.extract_document
    with patch(
        "document_intake.services.extraction.get_provider",
        return_value=FakeProvider(error=ProviderUnavailable("down")),
    ):
        task.push_request(retries=3, is_eager=False)
        try:
            task.run(str(doc.id))
        finally:
            task.pop_request()
    doc.refresh_from_db()
    assert doc.status == DocumentStatus.FAILED


def test_missing_file_fails_cleanly() -> None:
    doc = text_pdf_document()
    doc.file.storage.delete(doc.file.name)
    run_task(doc, FakeProvider(BANK_STATEMENT))
    assert doc.status == DocumentStatus.FAILED


def test_corrupt_pdf_fails_cleanly() -> None:
    doc = DocumentFactory(doc_type=DocType.RECEIPT)
    doc.file.save("x.pdf", ContentFile(b"%PDF-1.4 garbage"), save=True)
    run_task(doc, FakeProvider(RECEIPT))
    assert doc.status == DocumentStatus.FAILED
    assert "cannot be read" in doc.error_message
