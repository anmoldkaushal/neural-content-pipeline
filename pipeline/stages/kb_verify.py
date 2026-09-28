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


def stamp_provenance(client_dir: Path, entries: list[KBEntry], confirmed_by: str) -> None:
    """Appends each entry to provenance.json as a confirmed record, skipping any already present
    by claim text. Shared by approve() (kb-verify) and kb_add.add_claim()'s --verified path, so a
    fact is stamped identically whether it came from a document or a human asserting it directly."""
    provenance_path = client_dir / "provenance.json"
    provenance_raw = (
        json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else []
    )
    provenance_entries = [ProvenanceEntry(**p) for p in provenance_raw]
    existing_claims = {p.claim.strip().lower() for p in provenance_entries}

    today = dt.date.today()
    for e in entries:
        if e.claim.strip().lower() not in existing_claims:
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
            existing_claims.add(e.claim.strip().lower())

    provenance_path.write_text(
        json.dumps([p.model_dump(mode="json") for p in provenance_entries], indent=2),
        encoding="utf-8",
    )


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

    stamp_provenance(client_dir, [e for e in entries if e.id in approved_ids], confirmed_by)
    return entries


def _load(client_dir: Path) -> list[KBEntry]:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    return [KBEntry(**e) for e in json.loads(index_path.read_text(encoding="utf-8"))]


def _save(client_dir: Path, entries: list[KBEntry]) -> None:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    index_path.write_text(
        json.dumps([e.model_dump(mode="json") for e in entries], indent=2), encoding="utf-8"
    )


def _withdraw_provenance(client_dir: Path, claims: set[str]) -> None:
    """Drops confirmations for these claims, so nothing downstream still treats them as confirmed."""
    provenance_path = client_dir / "provenance.json"
    if not provenance_path.exists() or not claims:
        return
    kept = [
        p for p in json.loads(provenance_path.read_text(encoding="utf-8"))
        if str(p.get("claim", "")).strip().lower() not in claims
    ]
    provenance_path.write_text(json.dumps(kept, indent=2), encoding="utf-8")


def reject(client_dir: Path, entry_ids: list[str], confirmed_by: str) -> list[KBEntry]:
    """The delete path: marks entries 'rejected' rather than removing them, because kb_compile
    skips any claim already in the index -- a hard-deleted fact would be re-proposed on the next
    compile. A rejected fact that had been verified also leaves provenance.json."""
    entries = _load(client_dir)
    rejected_ids = set(entry_ids)
    rejected_claims: set[str] = set()
    for e in entries:
        if e.id in rejected_ids:
            e.status = "rejected"
            e.verified_at = dt.date.today()
            e.confirmed_by = confirmed_by
            rejected_claims.add(e.claim.strip().lower())
    _save(client_dir, entries)
    _withdraw_provenance(client_dir, rejected_claims)
    return entries


def exclude(client_dir: Path, reasons: dict[str, tuple[str, str]], only_pending: bool = True) -> list[str]:
    """Moves entries out of the review queue: {entry_id: (kind, reason)}. Excluded entries stay
    in the index (so a re-compile won't re-propose them) but are never usable in a draft and never
    need review. Returns the ids actually changed; with only_pending, anything a human has already
    decided on since is left alone."""
    entries = _load(client_dir)
    changed: list[str] = []
    for e in entries:
        if e.id in reasons and (e.status == "pending" or not only_pending):
            e.kind, e.exclusion_reason = reasons[e.id]
            e.status = "excluded"
            changed.append(e.id)
    _save(client_dir, entries)
    _withdraw_provenance(client_dir, {e.claim.strip().lower() for e in entries if e.id in changed})
    return changed


def restore(client_dir: Path, entry_ids: list[str]) -> list[KBEntry]:
    """Puts excluded entries back in the review queue as client claims."""
    entries = _load(client_dir)
    for e in entries:
        if e.id in set(entry_ids) and e.status == "excluded":
            e.status, e.kind, e.exclusion_reason = "pending", "claim", None
    _save(client_dir, entries)
    return entries


def purge(client_dir: Path, entry_ids: list[str]) -> int:
    """Removes entries from the index entirely -- for personal data that shouldn't be kept on
    disk at all. Unlike reject(), a later compile of the same document could re-extract them;
    the compile prompt's 'personal' kind is what stops that."""
    entries = _load(client_dir)
    drop = [e for e in entries if e.id in set(entry_ids)]
    _save(client_dir, [e for e in entries if e.id not in set(entry_ids)])
    _withdraw_provenance(client_dir, {e.claim.strip().lower() for e in drop})
    return len(drop)
