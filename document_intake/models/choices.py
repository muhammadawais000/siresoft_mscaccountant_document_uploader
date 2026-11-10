"""Enumerations shared by models, services, serializers and the UI."""

from django.db import models


class DocType(models.TextChoices):
    AUTO = "auto", "Auto-detect"
    BANK_STATEMENT = "bank_statement", "Bank statement"
    CREDIT_CARD_STATEMENT = "credit_card_statement", "Credit card statement"
    RECEIPT = "receipt", "Receipt"
    INVOICE = "invoice", "Invoice / bill"


class DocumentStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    PROCESSING = "processing", "Processing"
    NEEDS_REVIEW = "needs_review", "Needs review"
    NEEDS_CLARIFICATION = "needs_clarification", "Needs clarification"
    APPROVED = "approved", "Approved"
    EXPORTED = "exported", "Exported"
    REJECTED = "rejected", "Rejected"
    DUPLICATE = "duplicate", "Duplicate"
    FAILED = "failed", "Failed"


class ExtractionMethod(models.TextChoices):
    PDF_TEXT = "pdf_text", "PDF text layer"
    VISION = "vision", "Vision model"


class FieldStatus(models.TextChoices):
    OK = "ok", "OK"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"
    MISSING = "missing", "Missing"


class Severity(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class AuditAction(models.TextChoices):
    UPLOAD = "upload", "Upload"
    EXTRACT = "extract", "Extraction"
    STATUS_CHANGE = "status_change", "Status change"
    EDIT = "edit", "Edit"
    APPROVE = "approve", "Approve"
    REJECT = "reject", "Reject"
    REPROCESS = "reprocess", "Reprocess"
    DOWNLOAD = "download", "Download"
    EXPORT = "export", "Export"
    DELETE = "delete", "Soft delete"
