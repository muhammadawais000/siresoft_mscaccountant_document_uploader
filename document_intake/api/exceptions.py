"""Consistent JSON error format for the intake API.

Every error response has the shape::

    {"error": {"code": "<machine_code>", "message": "<human text>", "details": {...}}}
"""

from typing import Any

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from document_intake import exceptions as intake_exc

_STATUS_FOR: dict[type[intake_exc.IntakeError], int] = {
    intake_exc.UploadRejected: status.HTTP_400_BAD_REQUEST,
    intake_exc.InvalidTransition: status.HTTP_409_CONFLICT,
    intake_exc.DocumentTooLarge: status.HTTP_400_BAD_REQUEST,
}


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Wrap DRF and intake errors in the standard envelope."""
    if isinstance(exc, intake_exc.IntakeError):
        http_status = next(
            (code for cls, code in _STATUS_FOR.items() if isinstance(exc, cls)),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        return Response(
            {"error": {"code": exc.code, "message": str(exc), "details": {}}},
            status=http_status,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    data = response.data
    code = getattr(exc, "default_code", "error")
    if isinstance(data, dict) and set(data) == {"detail"}:
        message, details = str(data["detail"]), {}
        code = getattr(data["detail"], "code", code)
    else:
        message, details = "Invalid request.", data
    response.data = {"error": {"code": code, "message": message, "details": details}}
    return response
