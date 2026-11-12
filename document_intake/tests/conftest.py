"""Shared fixtures: API clients, users and generated sample files."""

import io
from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from rest_framework.test import APIClient

from document_intake.tests.factories import UserFactory


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def other_user():
    return UserFactory()


@pytest.fixture
def staff_user():
    return UserFactory(is_staff=True)


@pytest.fixture
def auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def staff_client(staff_user) -> APIClient:
    """The firm's reviewer: only staff may edit, approve, reject and export."""
    client = APIClient()
    client.force_authenticate(staff_user)
    return client


def make_pdf(lines: list[str] | None = None) -> bytes:
    """A one-page PDF with a real text layer."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    y = 720
    for line in lines or ["Sample Bank", "Statement period 2026-09-01 to 2026-09-30"]:
        pdf.drawString(72, y, line)
        y -= 18
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def make_image(fmt: str = "PNG", size: tuple[int, int] = (40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def pdf_bytes() -> bytes:
    return make_pdf()


@pytest.fixture
def make_upload() -> Callable[..., SimpleUploadedFile]:
    def _make(name: str, content: bytes, content_type: str = "application/octet-stream"):
        return SimpleUploadedFile(name, content, content_type=content_type)

    return _make
