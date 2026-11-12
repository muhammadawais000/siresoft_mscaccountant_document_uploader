"""Permission classes and the settings hook that selects them."""

from __future__ import annotations

from typing import Any

from django.utils.module_loading import import_string
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from document_intake.conf import intake_settings
from document_intake.services.access import can_access


class IsOwnerOrStaff(BasePermission):
    """Authenticated users act on their own documents; staff on all."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        return can_access(request.user, obj)


class IsStaffReviewer(BasePermission):
    """Only staff (the firm's team) may edit, approve, reject or export.

    Meant for REVIEW_PERMISSION_CLASSES: clients can still upload and view their own
    documents (PERMISSION_CLASSES), but the accounting decision stays with the firm.
    """

    message = "Only the firm's staff can review documents."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


def configured_permission_classes() -> list[type[BasePermission]]:
    """Permission classes named in ``DOCUMENT_INTAKE["PERMISSION_CLASSES"]``."""
    return [import_string(path) for path in intake_settings.PERMISSION_CLASSES]


def configured_review_permission_classes() -> list[type[BasePermission]]:
    """Extra classes for edit / approve / reject / export (``REVIEW_PERMISSION_CLASSES``)."""
    return [import_string(path) for path in intake_settings.REVIEW_PERMISSION_CLASSES]
