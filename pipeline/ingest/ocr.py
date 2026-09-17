"""OCR fallback for scanned PDFs and standalone images: pdf2image rasterizes pages, pytesseract
reads them. Requires the `tesseract` and `poppler` system binaries — neither is pip-installable,
so callers (and tests) check availability before relying on this path rather than crashing."""
from __future__ import annotations

import shutil
from pathlib import Path

from pipeline.schemas import IngestedDocument

TESSERACT_BIN = "tesseract"
POPPLER_BIN = "pdftoppm"  # part of poppler-utils; pdf2image shells out to it


def tesseract_available() -> bool:
    return shutil.which(TESSERACT_BIN) is not None


def poppler_available() -> bool:
    return shutil.which(POPPLER_BIN) is not None


def ingest_image(path: Path, doc_id: str) -> IngestedDocument:
    fmt = path.suffix.lstrip(".")
    if not tesseract_available():
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format=fmt,
            extraction_method="ocr",
            raw_text="",
            warnings=["tesseract binary not found on PATH; cannot OCR this image"],
        )
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format=fmt,
            extraction_method="ocr",
            raw_text="",
            warnings=[f"OCR dependency not installed: {exc}"],
        )

    try:
        text = pytesseract.image_to_string(Image.open(path))
    except Exception as exc:  # noqa: BLE001
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format=fmt,
            extraction_method="ocr",
            raw_text="",
            warnings=[f"OCR failed: {exc}"],
        )

    return IngestedDocument(
        doc_id=doc_id, source_path=str(path), format=fmt, extraction_method="ocr", raw_text=text
    )


def ingest_pdf_via_ocr(path: Path, doc_id: str) -> IngestedDocument:
    missing = []
    if not tesseract_available():
        missing.append("tesseract")
    if not poppler_available():
        missing.append("poppler (pdftoppm)")
    if missing:
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format="pdf",
            extraction_method="ocr",
            raw_text="",
            warnings=[f"missing system binaries for OCR: {', '.join(missing)}"],
        )

    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as exc:
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format="pdf",
            extraction_method="ocr",
            raw_text="",
            warnings=[f"OCR dependency not installed: {exc}"],
        )

    try:
        images = convert_from_path(str(path))
    except Exception as exc:  # noqa: BLE001
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format="pdf",
            extraction_method="ocr",
            raw_text="",
            warnings=[f"failed to rasterize pdf for OCR: {exc}"],
        )

    page_texts = [pytesseract.image_to_string(img) for img in images]
    return IngestedDocument(
        doc_id=doc_id,
        source_path=str(path),
        format="pdf",
        extraction_method="ocr",
        page_count=len(images),
        raw_text="\n\n".join(page_texts),
    )
