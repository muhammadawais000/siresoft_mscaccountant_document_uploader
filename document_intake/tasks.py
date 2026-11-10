"""Celery tasks. The extraction pipeline itself is implemented in delivery step 4."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="document_intake.extract_document")
def extract_document(document_id: str) -> None:
    """Run extraction for one document (placeholder until step 4)."""
    logger.info("extraction queued", extra={"document_id": document_id})
