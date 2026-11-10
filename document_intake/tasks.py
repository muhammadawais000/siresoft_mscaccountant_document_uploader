"""Celery tasks."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from document_intake.conf import intake_settings
from document_intake.exceptions import IntakeError, MalformedModelOutput, ProviderUnavailable
from document_intake.services.extraction import mark_failed, run_extraction

logger = logging.getLogger(__name__)

RETRYABLE = (ProviderUnavailable, MalformedModelOutput)


@shared_task(bind=True, name="document_intake.extract_document", acks_late=True)
def extract_document(self: Any, document_id: str) -> None:
    """Extract one document. Idempotent; safe to re-deliver.

    Provider outages and malformed output are retried with exponential backoff; after
    TASK_MAX_RETRIES (or immediately for permanent errors) the document is marked
    ``failed`` with a reason an operator can act on.
    """
    try:
        run_extraction(document_id)
    except RETRYABLE as exc:
        retries = self.request.retries
        if not self.request.is_eager and retries < intake_settings.TASK_MAX_RETRIES:
            countdown = intake_settings.TASK_RETRY_BACKOFF_SECONDS * (2**retries)
            logger.warning(
                "extraction will retry",
                extra={"document_id": document_id, "retry": retries + 1, "error": exc.code},
            )
            raise self.retry(exc=exc, countdown=countdown) from exc
        mark_failed(document_id, str(exc))
    except IntakeError as exc:
        mark_failed(document_id, str(exc))
    except Exception:
        logger.exception("unexpected extraction error", extra={"document_id": document_id})
        mark_failed(document_id, "Unexpected error during extraction. See worker logs.")
