"""Plain .txt/.md passthrough."""
from __future__ import annotations

from pathlib import Path

from pipeline.schemas import IngestedDocument


def ingest(path: Path, doc_id: str) -> IngestedDocument:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return IngestedDocument(
            doc_id=doc_id,
            source_path=str(path),
            format=path.suffix.lstrip("."),
            extraction_method="native",
            raw_text="",
            warnings=[f"could not read file: {exc}"],
        )
    return IngestedDocument(
        doc_id=doc_id,
        source_path=str(path),
        format=path.suffix.lstrip(".") or "txt",
        extraction_method="native",
        raw_text=raw,
    )
