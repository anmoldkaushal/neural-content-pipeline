"""python-docx extraction: paragraphs + table cell text, in document order."""
from __future__ import annotations

from pathlib import Path

from pipeline.schemas import IngestedDocument


def ingest(path: Path, doc_id: str) -> IngestedDocument:
    try:
        import docx  # python-docx
    except ImportError:
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format="docx",
            extraction_method="native",
            raw_text="",
            warnings=["python-docx is not installed"],
        )

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format="docx",
            extraction_method="native",
            raw_text="",
            warnings=[f"failed to open docx: {exc}"],
        )

    parts: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    raw_text = "\n".join(parts)
    warnings = [] if raw_text.strip() else ["no extractable text found in docx (possibly image-only content)"]

    return IngestedDocument(
        doc_id=doc_id,
        source_path=str(path),
        format="docx",
        extraction_method="native",
        raw_text=raw_text,
        warnings=warnings,
    )
