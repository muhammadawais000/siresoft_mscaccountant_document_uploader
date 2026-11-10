"""Request-ID propagation for structured logs.

Log IDs and statuses only. Never log document contents, amounts or extracted text.
"""

import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("intake_request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Adds ``request_id`` to every record so formatters can include it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True
