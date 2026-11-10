"""Middleware that assigns each request an ID for log correlation."""

import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from .logging import request_id_var

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
HEADER = "X-Request-ID"


class RequestIdMiddleware:
    """Reuses a well-formed incoming X-Request-ID (e.g. from Nginx) or creates one."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(HEADER, "")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response[HEADER] = request_id
        return response
