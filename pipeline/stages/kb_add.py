"""Direct human-asserted facts: kb_compile.py only ever adds pending entries extracted from a
document by an LLM. This is the first-hand counterpart -- a human telling the system something
directly (a call, an email, a confirmation from the client), with no document to extract from.

Also the only way to retire a fact a newer confirmation contradicts: kb_compile.py's own docstring
notes a conflicting new claim becomes its own pending entry rather than overwriting the old one,
but nothing ever resolved that conflict. --supersedes closes that gap by marking the old entry
'stale' so entailment.py stops treating it as usable, without silently deleting the record of what
used to be believed true."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Optional

from pipeline.schemas import KBEntry
from pipeline.stages.kb_verify import stamp_provenance


def _load_index(client_dir: Path) -> list[KBEntry]:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    if not index_path.exists():
        return []
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    return [KBEntry(**e) for e in raw]


def _save_index(client_dir: Path, entries: list[KBEntry]) -> None:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    index_path.write_text(
        json.dumps([e.model_dump(mode="json") for e in entries], indent=2), encoding="utf-8"
    )


def add_claim(
    client_dir: Path,
    claim: str,
    source: str,
    confirmed_by: str,
    verified: bool = False,
    supersedes: Optional[str] = None,
) -> KBEntry:
    """Adds a new KBEntry. verified=True stamps it straight to 'verified' (the human calling this
    IS the source, so it skips the kb-verify round-trip an LLM-extracted claim needs). supersedes
    marks an existing entry id 'stale' and links it to the replacement."""
    entries = _load_index(client_dir)

    if supersedes is not None and not any(e.id == supersedes for e in entries):
        raise ValueError(f"no such KB entry to supersede: {supersedes!r}")

    new_entry = KBEntry(
        id=f"kb-{uuid.uuid4().hex[:8]}",
        claim=claim,
        source_doc=source,
        status="verified" if verified else "pending",
    )
    if verified:
        new_entry.verified_at = dt.date.today()
        new_entry.confirmed_by = confirmed_by

    if supersedes is not None:
        for e in entries:
            if e.id == supersedes:
                e.status = "stale"
                e.superseded_by = new_entry.id

    entries.append(new_entry)
    _save_index(client_dir, entries)

    if verified:
        stamp_provenance(client_dir, [new_entry], confirmed_by)

    return new_entry
