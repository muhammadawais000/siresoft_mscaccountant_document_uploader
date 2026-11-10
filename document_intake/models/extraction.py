"""Extraction output: one versioned result per run, with fields, lines and issues."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from .base import SoftDeleteModel, TimeStampedModel
from .choices import ExtractionMethod, FieldStatus, Severity
from .document import Document


def money_field() -> models.DecimalField[Decimal | int | str | None, Decimal | None]:
    """Nullable money column. Amounts are always Decimal, never float."""
    return models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)


class ExtractionResult(TimeStampedModel):
    """One extraction run. Re-running creates version n+1; history is never overwritten."""

    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="results")
    version = models.PositiveIntegerField()
    raw_text = models.TextField(blank=True, default="")
    structured_data = models.JSONField(default=dict, blank=True)
    extraction_method = models.CharField(max_length=16, choices=ExtractionMethod.choices)
    provider = models.CharField(max_length=64)
    model_name = models.CharField(max_length=128)
    prompt_version = models.CharField(max_length=32)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("document", "-version")
        constraints = [
            models.UniqueConstraint(fields=["document", "version"], name="intake_result_version"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="intake_result_v_pos"),
        ]

    def __str__(self) -> str:
        return f"Result v{self.version} for {self.document_id}"


class ExtractedField(TimeStampedModel):
    """A header-level value such as vendor, invoice date or closing balance."""

    result = models.ForeignKey(ExtractionResult, on_delete=models.PROTECT, related_name="fields")
    key = models.CharField(max_length=64)
    label = models.CharField(max_length=128)
    position = models.PositiveSmallIntegerField(default=0)
    value = models.TextField(blank=True, default="", help_text="As extracted (or as edited).")
    normalized_value = models.TextField(
        blank=True, default="", help_text="Canonical form: ISO date, decimal string, etc."
    )
    status = models.CharField(max_length=16, choices=FieldStatus.choices, default=FieldStatus.OK)
    status_reason = models.CharField(max_length=255, blank=True, default="")
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("result", "position", "key")
        constraints = [
            models.UniqueConstraint(fields=["result", "key"], name="intake_field_key"),
            models.CheckConstraint(
                condition=models.Q(status__in=FieldStatus.values), name="intake_field_status_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.key


class ExtractedLine(SoftDeleteModel):
    """A transaction (statements) or line item (receipts and invoices)."""

    result = models.ForeignKey(ExtractionResult, on_delete=models.PROTECT, related_name="lines")
    line_no = models.PositiveIntegerField()
    date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    debit = money_field()
    credit = money_field()
    amount = money_field()
    balance = money_field()
    tax = money_field()
    category = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=16, choices=FieldStatus.choices, default=FieldStatus.OK)
    status_reason = models.CharField(max_length=255, blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta(SoftDeleteModel.Meta):
        ordering = ("result", "line_no")
        constraints = [
            models.UniqueConstraint(fields=["result", "line_no"], name="intake_line_no"),
            models.CheckConstraint(condition=models.Q(line_no__gte=1), name="intake_line_no_pos"),
            models.CheckConstraint(
                condition=models.Q(debit__isnull=True) | models.Q(debit__gte=0),
                name="intake_line_debit_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(credit__isnull=True) | models.Q(credit__gte=0),
                name="intake_line_credit_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=FieldStatus.values), name="intake_line_status_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"Line {self.line_no}"


class ValidationIssue(TimeStampedModel):
    """A finding from the validation engine, tied to a field, a line, or the whole document."""

    result = models.ForeignKey(ExtractionResult, on_delete=models.PROTECT, related_name="issues")
    rule_code = models.CharField(max_length=64)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    field_key = models.CharField(max_length=64, blank=True, default="")
    line_no = models.PositiveIntegerField(null=True, blank=True)
    message = models.CharField(max_length=500)
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("result", "line_no", "created_at")
        indexes = [models.Index(fields=["result", "resolved"], name="intake_issue_open")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(severity__in=Severity.values), name="intake_issue_severity_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.rule_code
