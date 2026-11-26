"""Step 4: PDF text layer, page rendering, image normalisation, routing."""

import io

import pytest
from django.test import override_settings
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from document_intake.exceptions import DocumentTooLarge, ExtractionError
from document_intake.extraction import sources
from document_intake.models import ExtractionMethod
from document_intake.services.extraction import prepare_input
from document_intake.tests.conftest import make_image, make_pdf


def scanned_pdf(pages: int = 1) -> bytes:
    """An image-only PDF (no text layer), like a scanner produces."""
    images = [Image.new("RGB", (300, 400), "white") for _ in range(pages)]
    out = io.BytesIO()
    images[0].save(out, format="PDF", save_all=True, append_images=images[1:])
    return out.getvalue()


def encrypted_pdf() -> bytes:
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=letter, encrypt="secret")
    pdf.drawString(72, 720, "Confidential")
    pdf.save()
    return out.getvalue()


def statement_pdf(pages: int = 1) -> bytes:
    lines = [f"Transaction line {i} with enough text to count" for i in range(5)]
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=letter)
    for _ in range(pages):
        for i, line in enumerate(lines):
            pdf.drawString(72, 720 - 18 * i, line)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def test_text_pdf_has_text_layer() -> None:
    result = sources.read_pdf_text(io.BytesIO(statement_pdf(2)))
    assert result.page_count == 2
    assert result.has_text_layer()
    assert "--- Page 2 ---" in result.text
    assert "Transaction line 0" in result.text


def test_scanned_pdf_has_no_text_layer() -> None:
    result = sources.read_pdf_text(io.BytesIO(scanned_pdf()))
    assert result.page_count == 1
    assert not result.has_text_layer()


def test_tables_are_included() -> None:
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=letter)
    # A ruled 2x2 grid that pdfplumber detects as a table.
    for x in (100, 250, 400):
        pdf.line(x, 600, x, 680)
    for y in (600, 640, 680):
        pdf.line(100, y, 400, y)
    pdf.drawString(110, 655, "Date")
    pdf.drawString(260, 655, "Amount")
    pdf.drawString(110, 615, "2026-09-03")
    pdf.drawString(260, 615, "1500.00")
    pdf.save()
    text = sources.read_pdf_text(io.BytesIO(out.getvalue())).text
    assert "[Tables]" in text
    assert "2026-09-03 | 1500.00" in text


def test_too_many_pages() -> None:
    with (
        override_settings(DOCUMENT_INTAKE={"MAX_PAGES": 1}),
        pytest.raises(DocumentTooLarge),
    ):
        sources.read_pdf_text(io.BytesIO(statement_pdf(2)))


def test_encrypted_pdf_is_a_clear_error() -> None:
    with pytest.raises(ExtractionError, match="encrypted"):
        sources.read_pdf_text(io.BytesIO(encrypted_pdf()))


def test_render_pages_to_png() -> None:
    images = sources.render_pdf_pages(scanned_pdf(2))
    assert len(images) == 2
    assert images[0].startswith(b"\x89PNG")


def test_render_limit_for_vision() -> None:
    with (
        override_settings(DOCUMENT_INTAKE={"MAX_VISION_PAGES": 1}),
        pytest.raises(DocumentTooLarge, match="limited to 1"),
    ):
        sources.render_pdf_pages(scanned_pdf(2))


def test_render_garbage_pdf() -> None:
    with pytest.raises(ExtractionError):
        sources.render_pdf_pages(b"%PDF-1.4 broken")


def test_load_image_applies_exif_rotation_and_caps_size() -> None:
    img = Image.new("RGB", (4000, 1000), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees on display
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)

    (png,) = sources.load_image(out.getvalue())
    with Image.open(io.BytesIO(png)) as result:
        assert result.format == "PNG"
        assert result.size[1] > result.size[0]  # portrait after rotation
        assert max(result.size) <= 2000


def test_load_image_converts_mode() -> None:
    img = Image.new("RGBA", (10, 10))
    out = io.BytesIO()
    img.save(out, format="PNG")
    (png,) = sources.load_image(out.getvalue())
    with Image.open(io.BytesIO(png)) as result:
        assert result.mode == "RGB"


def test_load_image_garbage() -> None:
    with pytest.raises(ExtractionError):
        sources.load_image(b"not an image")


# ----------------------------------------------------------------- routing


def test_route_text_pdf() -> None:
    prepared = prepare_input(statement_pdf(), "application/pdf")
    assert prepared.method == ExtractionMethod.PDF_TEXT
    assert prepared.images == ()
    assert not prepared.vision


def test_route_scanned_pdf() -> None:
    prepared = prepare_input(scanned_pdf(), "application/pdf")
    assert prepared.method == ExtractionMethod.VISION
    assert len(prepared.images) == 1
    assert prepared.text == ""


def test_route_image() -> None:
    prepared = prepare_input(make_image("JPEG"), "image/jpeg")
    assert prepared.method == ExtractionMethod.VISION
    assert prepared.page_count == 1


def test_route_text_too_long() -> None:
    with (
        override_settings(DOCUMENT_INTAKE={"MAX_TEXT_CHARS": 10}),
        pytest.raises(DocumentTooLarge),
    ):
        prepare_input(statement_pdf(), "application/pdf")


def test_route_unknown_type() -> None:
    with pytest.raises(ExtractionError):
        prepare_input(make_pdf(), "text/plain")


def masked_scan_pdf() -> bytes:
    """A scan with only a redaction layer as text, like the RBC sample."""
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=letter)
    for i in range(6):
        pdf.drawString(72, 720 - 14 * i, "x" * 30)
    pdf.drawInlineImage(Image.new("RGB", (200, 260), "white"), 72, 300)
    pdf.showPage()
    pdf.save()
    return out.getvalue()


def test_redaction_mask_is_not_a_text_layer() -> None:
    result = sources.read_pdf_text(io.BytesIO(masked_scan_pdf()))
    assert "xxxxxxxx" in result.text
    assert not result.has_text_layer()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("xxxxxxxxxxxx\nXXXX ****", 0),
        ("4516 07** **** 6946", 10),
        ("PAYMENT - THANK YOU -$4,000.00", 21),
    ],
)
def test_readable_chars(text, expected) -> None:
    assert sources.readable_chars(text) == expected
