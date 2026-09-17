"""Ingestion tests. Generates its own .docx and .pdf fixtures at test time (python-docx, and
reportlab as a dev-only dependency) rather than committing binary files. OCR-dependent assertions
skip gracefully when tesseract/poppler aren't installed on the machine running the suite."""
from __future__ import annotations

import pytest

from pipeline.ingest import ingest_document
from pipeline.ingest.ocr import poppler_available, tesseract_available

TESSERACT_MISSING = not tesseract_available()
POPPLER_MISSING = not poppler_available()


def test_text_ingest(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("Hello from a plain text fixture.", encoding="utf-8")
    doc = ingest_document(p)
    assert doc.format == "txt"
    assert doc.extraction_method == "native"
    assert "Hello" in doc.raw_text
    assert doc.warnings == []


def test_docx_ingest(tmp_path):
    docx = pytest.importorskip("docx")
    p = tmp_path / "sample.docx"
    document = docx.Document()
    document.add_paragraph("A synthetic paragraph for the ingestion test.")
    document.save(str(p))

    doc = ingest_document(p)
    assert doc.format == "docx"
    assert "synthetic paragraph" in doc.raw_text


def test_pdf_text_layer_ingest(tmp_path):
    """Does NOT depend on tesseract/poppler -- this is the text-layer path, which must run
    everywhere."""
    reportlab_canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    p = tmp_path / "sample.pdf"
    c = reportlab_canvas.Canvas(str(p))
    c.drawString(72, 720, "A synthetic sentence with a real text layer for extraction testing.")
    c.save()

    doc = ingest_document(p)
    assert doc.format == "pdf"
    assert doc.extraction_method == "text_layer"
    assert "synthetic sentence" in doc.raw_text


@pytest.mark.skipif(TESSERACT_MISSING or POPPLER_MISSING, reason="tesseract/poppler not installed on this machine")
def test_pdf_ocr_fallback_on_image_only_pdf(tmp_path):
    """Builds a PDF with a rasterized-text image (no real text layer) and confirms pdf.py falls
    back to OCR rather than returning empty text."""
    reportlab_canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    from PIL import Image, ImageDraw

    img_path = tmp_path / "page.png"
    img = Image.new("RGB", (400, 100), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 40), "OCR FALLBACK TEXT", fill="black")
    img.save(img_path)

    pdf_path = tmp_path / "scanned.pdf"
    c = reportlab_canvas.Canvas(str(pdf_path))
    c.drawImage(str(img_path), 0, 0, width=400, height=100)
    c.save()

    doc = ingest_document(pdf_path)
    assert doc.extraction_method == "ocr"
    assert any("OCR fallback" in w for w in doc.warnings)


@pytest.mark.skipif(TESSERACT_MISSING, reason="tesseract not installed on this machine")
def test_image_ingest_via_ocr(tmp_path):
    from PIL import Image, ImageDraw

    img_path = tmp_path / "note.png"
    img = Image.new("RGB", (300, 80), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 30), "STANDALONE IMAGE", fill="black")
    img.save(img_path)

    doc = ingest_document(img_path)
    assert doc.extraction_method == "ocr"


def test_unsupported_extension_returns_warning_not_exception(tmp_path):
    p = tmp_path / "data.xyz"
    p.write_text("irrelevant", encoding="utf-8")
    doc = ingest_document(p)
    assert doc.warnings
    assert doc.raw_text == ""
