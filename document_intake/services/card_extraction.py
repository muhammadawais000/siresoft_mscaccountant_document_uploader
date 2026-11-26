"""Page-by-page extraction of a credit card statement.

One model call per page that can hold transactions, each answer checked with Pydantic, then
merged by code (``extraction.statement_processor``).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from document_intake.conf import intake_settings
from document_intake.exceptions import MalformedModelOutput
from document_intake.extraction import parsing, prompts, schemas
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
    data = processor.merge_pages(answers, pages)

    # Printed rows the model left out (for example the last row under a new card header):
    # ask once more with only those lines. Anything still missing fails the row check.
    by_page: dict[int, list[str]] = {}
    for missed in data["missed_lines"]:
        by_page.setdefault(missed["page"], []).extend(
            line for line in (missed["line"], missed["next_line"]) if line
        )
    for number, lines in by_page.items():
        user = prompts.missed_lines_prompt(task, number, lines)
        answer, model = _ask(provider, ModelRequest(system, user, schema), number)
        blank = processor.PrintedPage(
            summary=processor.PrintedSummary(),
            section_totals=[],
            transactions=answer.transactions,
        )
        answers.append((number, blank))
    if by_page:
        logger.info("asked again for missed rows", extra={"pages": sorted(by_page)})
        data = processor.merge_pages(answers, pages)
    _settle_balances(provider, prepared, data, pages, system, schema)
    return data, model


def _amount(value: Any) -> Decimal | None:
    try:
        return parsing.parse_printed_amount(value)
    except parsing.ParseError:
        return None


def _options(*values: Any) -> list[Decimal]:
    """Each read value, then the same without its sign, without repeats."""
    found: list[Decimal] = []
    for value in values:
        amount = _amount(value)
        if amount is None:
            continue
        for option in (amount, abs(amount)):
            if option not in found:
                found.append(option)
    return found


def _settle_balances(
    provider: ExtractionProvider,
    prepared: PreparedInput,
    data: dict[str, Any],
    pages: list[str],
    system: str,
    schema: dict[str, Any],
) -> None:
    """When previous - credits + debits is not the new balance, look again.

    Seen with qwen2.5:7b on Scotia: both balances came back with a minus. First the read
    values are tried without their sign; then the model is asked for the summary alone.
    A pair is taken only if the transactions explain it. Otherwise nothing changes and the
    closing check fails as before.
    """
    lines = parsing.build_lines(DocType.CREDIT_CARD_STATEMENT, data)
    if not lines or any(line.debit is None and line.credit is None for line in lines):
        return  # missing amounts: the row check reports that, balances cannot be judged
    printed_rows = processor.count_rows(pages)
    if printed_rows is not None and printed_rows != len(lines):
        return  # rows are missing or extra: that explains the gap, the row check reports it
    debits = sum((line.debit or Decimal("0") for line in lines), Decimal("0"))
    credits = sum((line.credit or Decimal("0") for line in lines), Decimal("0"))
    before, after = _amount(data["opening_balance"]), _amount(data["closing_balance"])
    if before is not None and after is not None and before - credits + debits == after:
        return
    previous = _options(data["opening_balance"])
    new = _options(data["closing_balance"])
    pair = processor.choose_balances(previous, new, credits, debits)
    if pair is None:
        if prepared.vision:
            request = ModelRequest(
                system,
                prompts.summary_prompt("(see the attached page image)"),
                schema,
                images=prepared.images[:1],
                vision=True,
            )
        elif pages:
            summary_page = next(
                (page for page in pages if processor.needs_model(page, 1)), pages[0]
            )
            request = ModelRequest(system, prompts.summary_prompt(summary_page), schema)
        else:
            return
        answer, _ = _ask(provider, request, 1)
        pair = processor.choose_balances(
            [*previous, *_options(answer.summary.previous_balance)],
            [*new, *_options(answer.summary.new_balance)],
            credits,
            debits,
        )
    if pair is None:
        return
    data["opening_balance"], data["closing_balance"] = str(pair[0]), str(pair[1])
    data["summary"]["previous_balance"], data["summary"]["new_balance"] = map(str, pair)
    data["balances_rechecked"] = True
    logger.info("balances re-read")
