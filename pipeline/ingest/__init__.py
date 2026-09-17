"""Dispatches document ingestion by file extension. Every path returns an IngestedDocument;
nothing here raises on an unreadable file — it returns a warning instead so kb_compile.py can
surface it without crashing a whole compile run over one bad file."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Union

from pipeline.ingest import docx as _docx
from pipeline.ingest import ocr as _ocr
from pipeline.ingest import pdf as _pdf
from pipeline.ingest import text as _text
from pipeline.schemas import IngestedDocument

_TEXT_EXTS = {".txt", ".md"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}

__all__ = ["ingest_document"]


def ingest_document(path: Union[str, Path]) -> IngestedDocument:
    path = Path(path)
    doc_id = uuid.uuid4().hex[:12]
    suffix = path.suffix.lower()

    if suffix in _TEXT_EXTS:
        return _text.ingest(path, doc_id)
    if suffix == ".docx":
        return _docx.ingest(path, doc_id)
    if suffix == ".pdf":
        return _pdf.ingest(path, doc_id)
    if suffix in _IMAGE_EXTS:
        return _ocr.ingest_image(path, doc_id)

    return IngestedDocument(
        doc_id=doc_id,
        source_path=str(path),
        format=suffix.lstrip(".") or "unknown",
        extraction_method="native",
        raw_text="",
        warnings=[f"unsupported file extension {suffix!r}; no text extracted"],
    )
