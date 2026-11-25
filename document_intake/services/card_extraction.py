"""Page-by-page extraction of a credit card statement.

One model call per page that can hold transactions, each answer checked with Pydantic, then
merged by code (``extraction.statement_processor``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from document_intake.conf import intake_settings
from document_intake.exceptions import MalformedModelOutput
from document_intake.extraction import prompts, schemas
from document_intake.extraction import statement_processor as processor
from document_intake.models import DocType
from document_intake.providers.base import ExtractionProvider, ModelRequest

if TYPE_CHECKING:
    from .extraction import PreparedInput

logger = logging.getLogger(__name__)

ATTEMPTS = 2  # the first call plus one retry


def _ask(
    provider: ExtractionProvider, request: ModelRequest, page: int
) -> tuple[processor.PrintedPage, str]:
    for attempt in range(ATTEMPTS):
        response = provider.complete_json(request)
        try:
            return processor.PrintedPage.model_validate(response.data), response.model_name
        except ValidationError as exc:
            # Only counts go to the log: the error text would contain document content.
            logger.warning(
                "card page did not validate",
                extra={"page": page, "attempt": attempt + 1, "errors": exc.error_count()},
            )
    raise MalformedModelOutput(f"Page {page}: the model output does not match the schema.")


def extract_card_statement(
    provider: ExtractionProvider, prepared: PreparedInput
) -> tuple[dict[str, Any], str]:
    """(merged statement data, model name)."""
    system = prompts.card_system_prompt(str(intake_settings.DEFAULT_CURRENCY))
    task = prompts.TASK_PROMPTS[DocType.CREDIT_CARD_STATEMENT]
    schema = schemas.SCHEMA_FOR_TYPE[DocType.CREDIT_CARD_STATEMENT]
    pages = processor.split_pages(prepared.text)

    requests: list[tuple[int, ModelRequest]] = []
    if prepared.vision:
        count = len(prepared.images)
        for number, image in enumerate(prepared.images, 1):
            user = prompts.card_page_prompt(task, number, count, "")
            request = ModelRequest(system, user, schema, images=(image,), vision=True)
            requests.append((number, request))
    else:
        layout = processor.read_layout(pages)
        for number, page in enumerate(pages, 1):
            if not processor.needs_model(page, number):
                continue
            user = prompts.card_page_prompt(task, number, len(pages), layout.first_card[number])
            request = ModelRequest(system, prompts.text_user_prompt(user, page), schema)
            requests.append((number, request))

    answers: list[tuple[int, processor.PrintedPage]] = []
    model = ""
    for number, request in requests:
        answer, model = _ask(provider, request, number)
        answers.append((number, answer))
    return processor.merge_pages(answers, pages), model
