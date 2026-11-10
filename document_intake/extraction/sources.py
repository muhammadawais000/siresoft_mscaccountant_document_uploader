"""Read stored documents: PDF text layer (pdfplumber) and page images (pypdfium2 / Pillow)."""

from __future__ import annotations

import io
from dataclasses import dataclass

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from document_intake.conf import intake_settings
from document_intake.exceptions import DocumentTooLarge, ExtractionError


@dataclass(frozen=True)
class PdfText:
    page_count: int
    pages: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(f"--- Page {i} ---\n{page}" for i, page in enumerate(self.pages, 1))

    def has_text_layer(self) -> bool:
        """True when the average page carries enough characters to be a digital PDF."""
        if not self.page_count:
            return False
        chars = sum(len(page.strip()) for page in self.pages)
        return bool(chars / self.page_count >= intake_settings.MIN_TEXT_CHARS_PER_PAGE)


def _format_tables(tables: list[list[list[str | None]]]) -> str:
    rows = [
        " | ".join((cell or "").replace("\n", " ").strip() for cell in row)
        for table in tables
        for row in table
    ]
    return "\n".join(rows)


def read_pdf_text(fp: io.BytesIO) -> PdfText:
    """Extract per-page text (and tables, as pipe-separated rows) from a PDF.

    Raises:
        DocumentTooLarge: more pages than MAX_PAGES.
        ExtractionError: encrypted or unreadable PDF.
    """
    try:
        with pdfplumber.open(fp) as pdf:
            page_count = len(pdf.pages)
            if page_count > intake_settings.MAX_PAGES:
                raise DocumentTooLarge(
                    f"The PDF has {page_count} pages; the limit is {intake_settings.MAX_PAGES}."
                )
            pages = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                tables = _format_tables(page.extract_tables())
                pages.append(f"{text}\n\n[Tables]\n{tables}" if tables else text)
    except DocumentTooLarge:
        raise
    except Exception as exc:
        # pdfminer raises a variety of types for encrypted / corrupt files.
        raise ExtractionError("The PDF is encrypted or cannot be read.") from exc
    return PdfText(page_count=page_count, pages=tuple(pages))


def _to_png(image: Image.Image) -> bytes:
    image = ImageOps.exif_transpose(image) or image
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    edge = int(intake_settings.MAX_IMAGE_EDGE_PX)
    image.thumbnail((edge, edge))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _check_vision_pages(count: int) -> None:
    limit = int(intake_settings.MAX_VISION_PAGES)
    if count > limit:
        raise DocumentTooLarge(
            f"Scanned document has {count} pages; vision extraction is limited to {limit}."
        )


def render_pdf_pages(data: bytes) -> list[bytes]:
    """Render each PDF page to PNG bytes at RENDER_DPI."""
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise ExtractionError("The PDF is encrypted or cannot be read.") from exc
    try:
        _check_vision_pages(len(pdf))
        scale = int(intake_settings.RENDER_DPI) / 72
        images = []
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                images.append(_to_png(page.render(scale=scale).to_pil()))
            finally:
                page.close()
        return images
    finally:
        pdf.close()


def load_image(data: bytes) -> list[bytes]:
    """Normalise a photo or scan: honour EXIF rotation, convert to RGB, cap the size."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return [_to_png(image)]
    except (OSError, Image.DecompressionBombError) as exc:
        raise ExtractionError("The image cannot be read.") from exc
