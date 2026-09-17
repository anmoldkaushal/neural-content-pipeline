"""PDF text-layer extraction via pypdf. Falls back to OCR (pipeline/ingest/ocr.py) when the
extracted text is too sparse to be a real text layer — the signature of a scanned/image PDF."""
from __future__ import annotations

from pathlib import Path

from pipeline.ingest import ocr as _ocr
from pipeline.schemas import IngestedDocument

MIN_CHARS_PER_PAGE = 40  # below this, assume the page has no real text layer


def ingest(path: Path, doc_id: str) -> IngestedDocument:
    try:
        from pypdf import PdfReader
    except ImportError:
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format="pdf",
            extraction_method="native",
            raw_text="",
            warnings=["pypdf is not installed"],
        )

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format="pdf",
            extraction_method="native",
            raw_text="",
            warnings=[f"failed to open pdf: {exc}"],
        )

    page_texts = [page.extract_text() or "" for page in reader.pages]
    page_count = len(page_texts)
    total_chars = sum(len(t) for t in page_texts)
    avg_chars_per_page = (total_chars / page_count) if page_count else 0

    if avg_chars_per_page >= MIN_CHARS_PER_PAGE:
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format="pdf",
            extraction_method="text_layer",
            page_count=page_count,
            raw_text="\n\n".join(page_texts),
        )

    ocr_result = _ocr.ingest_pdf_via_ocr(path, doc_id)
    ocr_result.warnings.insert(
        0,
        f"text layer averaged {avg_chars_per_page:.0f} chars/page (below {MIN_CHARS_PER_PAGE}); "
        f"used OCR fallback",
    )
    return ocr_result
