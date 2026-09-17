"""Renders only the delta (pending entries) for human approval — the single biggest lever for
cutting review load discussed in this repo's design conversation. Approving an entry stamps
clients/<id>/provenance.json with confirmed_by + date, same shape as GTM's provenance.json, and
marks the KB entry 'verified'."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pipeline.schemas import KBEntry, ProvenanceEntry


def pending_entries(client_dir: Path) -> list[KBEntry]:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    if not index_path.exists():
        return []
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    entries = [KBEntry(**e) for e in raw]
    return [e for e in entries if e.status == "pending"]


def approve(client_dir: Path, entry_ids: list[str], confirmed_by: str) -> list[KBEntry]:
    """Marks the given entry ids verified, stamps provenance.json, and persists both files."""
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    entries = [KBEntry(**e) for e in json.loads(index_path.read_text(encoding="utf-8"))]

    today = dt.date.today()
    approved_ids = set(entry_ids)
    for e in entries:
        if e.id in approved_ids:
            e.status = "verified"
            e.verified_at = today
            e.confirmed_by = confirmed_by

    index_path.write_text(
        json.dumps([e.model_dump(mode="json") for e in entries], indent=2), encoding="utf-8"
    )

    provenance_path = client_dir / "provenance.json"
    provenance_raw = (
        json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else []
    )
    provenance_entries = [ProvenanceEntry(**p) for p in provenance_raw]
    existing_claims = {p.claim.strip().lower() for p in provenance_entries}

    for e in entries:
        if e.id in approved_ids and e.claim.strip().lower() not in existing_claims:
            provenance_entries.append(
                ProvenanceEntry(
                    claim=e.claim,
                    type="fact",
                    source=e.source_doc,
                    confirmed=True,
                    confirmed_by=confirmed_by,
                    date=today,
                )
            )

    provenance_path.write_text(
        json.dumps([p.model_dump(mode="json") for p in provenance_entries], indent=2),
        encoding="utf-8",
    )
    return entries
