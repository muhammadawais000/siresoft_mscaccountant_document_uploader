"""Exception hierarchy for the intake module.

Messages here may reach API clients, so they must never contain document contents,
amounts or extracted text.
"""


class IntakeError(Exception):
    """Base class for all intake errors."""

    code = "intake_error"


class UploadRejected(IntakeError):
    """The uploaded file failed validation (type, size, content)."""

    code = "upload_rejected"


class InvalidTransition(IntakeError):
    """A document status change that the lifecycle does not allow."""

    code = "invalid_transition"


class ExtractionError(IntakeError):
    """Extraction failed in a way that retrying may fix."""

    code = "extraction_error"


class ProviderUnavailable(ExtractionError):
    """The model provider could not be reached or timed out."""

    code = "provider_unavailable"


class MalformedModelOutput(ExtractionError):
    """The model returned output that is not valid JSON or does not match the schema."""

    code = "malformed_model_output"


class DocumentTooLarge(IntakeError):
    """The document has more pages than MAX_PAGES."""

    code = "document_too_large"
