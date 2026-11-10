"""Dispatch rules by document type and derive per-field / per-line statuses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .receipt import validate_receipt
from .statement import validate_statement
from .types import SEVERITY_RANK, FieldInput, Issue, LineInput, RuleConfig, Sev

# Field / line status values (mirror models.FieldStatus).
OK, WARNING, ERROR, MISSING = "ok", "warning", "error", "missing"
_STATUS_RANK = {OK: 0, WARNING: 1, MISSING: 2, ERROR: 3}
_FROM_SEVERITY = {Sev.INFO: OK, Sev.WARNING: WARNING, Sev.ERROR: ERROR}


def validate(
    doc_type: str,
    fields: Mapping[str, FieldInput],
    lines: Sequence[LineInput],
    cfg: RuleConfig,
) -> list[Issue]:
    """Run every rule for ``doc_type``. Most severe first, then by position."""
    if doc_type == "bank_statement":
        issues = validate_statement(fields, lines, cfg)
    elif doc_type == "credit_card_statement":
        issues = validate_statement(fields, lines, cfg, credit_card=True)
    elif doc_type in ("receipt", "invoice"):
        issues = validate_receipt(fields, lines, cfg, invoice=doc_type == "invoice")
    else:
        raise ValueError(f"No validation rules for document type {doc_type!r}")
    return sorted(
        issues,
        key=lambda i: (-SEVERITY_RANK[i.severity], i.line_no or 0, i.field_key, i.rule_code),
    )


@dataclass(frozen=True)
class Status:
    status: str
    reason: str = ""


def _worse(current: Status, status: str, reason: str) -> Status:
    if _STATUS_RANK[status] > _STATUS_RANK[current.status]:
        return Status(status, reason)
    return current


def derive_statuses(
    fields: Mapping[str, FieldInput],
    lines: Sequence[LineInput],
    issues: Sequence[Issue],
    *,
    reviewer_keys: frozenset[str] = frozenset(),
) -> tuple[dict[str, Status], dict[int, Status]]:
    """The model gives no confidence score, so status comes from evidence:

    * ``missing``: blank (except reviewer-entered fields, which start blank)
    * ``error`` / ``warning``: the worst issue that points at the field or line
    * ``ok``: otherwise
    """
    field_status: dict[str, Status] = {}
    for key, item in fields.items():
        status = Status(OK)
        # A blank value with a parsed default (an assumed currency) is not missing.
        if item.is_blank and item.parsed is None and key not in reviewer_keys:
            status = Status(MISSING, "Not found or not legible")
        field_status[key] = status

    line_status: dict[int, Status] = {line.line_no: Status(OK) for line in lines}

    for issue in issues:
        mapped = _FROM_SEVERITY[issue.severity]
        if issue.line_no is not None and issue.line_no in line_status:
            line_status[issue.line_no] = _worse(line_status[issue.line_no], mapped, issue.message)
        elif issue.field_key in field_status:
            # A blank required field stays "missing" but gets the issue's message.
            current = field_status[issue.field_key]
            if current.status == MISSING:
                if issue.severity == Sev.ERROR:
                    field_status[issue.field_key] = Status(MISSING, issue.message)
                continue
            field_status[issue.field_key] = _worse(current, mapped, issue.message)
    return field_status, line_status
