import logging

from django.apps import AppConfig

# Third-party parsers that log document contents (text tokens, image data) at DEBUG.
CHATTY_LOGGERS = ("pdfminer", "pdfplumber", "PIL")


class DocumentIntakeConfig(AppConfig):
    name = "document_intake"
    verbose_name = "Document intake"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Never let document contents reach logs, even if the host turns on DEBUG logging.
        for name in CHATTY_LOGGERS:
            logger = logging.getLogger(name)
            if logger.level < logging.WARNING:
                logger.setLevel(logging.WARNING)
