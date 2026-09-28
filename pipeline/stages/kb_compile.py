"""Ingests every document dropped in a client's knowledge_base/documents/ folder, drafts new
kb_index.json entries, and diffs against what's already verified. Never overwrites an already-
verified entry silently — a conflicting new claim becomes its own pending entry for kb_verify.py
to surface, not an automatic replacement."""
from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Optional

from pipeline.ingest import ingest_document
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import KB_KINDS, KBEntry

# Never written to the index: project notes aren't copy material, and personal details about
# named private individuals shouldn't sit in a client's knowledge base at all.
_NEVER_STORED = {"internal", "personal"}

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "kb_compile.md"


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


def compile_kb(
    client_dir: Path, transport: Optional[ClaudeTransport] = None
) -> tuple[list[KBEntry], list[str]]:
    """Returns (all_entries_after_compile, warnings). Only statements labelled "claim" land as
    'pending' for review; style/audience/reference context is kept as 'excluded' with a reason,
    and internal or personal statements are not stored at all (reported in warnings)."""
    transport = transport or ClaudeTransport()
    existing = _load_index(client_dir)
    existing_claims = {e.claim.strip().lower() for e in existing}
    warnings: list[str] = []

    docs_dir = client_dir / "knowledge_base" / "documents"
    doc_paths = (
        [p for p in docs_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
        if docs_dir.exists()
        else []
    )

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    new_entries: list[KBEntry] = []

    for doc_path in doc_paths:
        ingested = ingest_document(doc_path)
        warnings.extend(f"{doc_path.name}: {w}" for w in ingested.warnings)
        if not ingested.raw_text.strip():
            continue

        prompt = f"{template}\n\n--- DOCUMENT: {doc_path.name} ---\n{ingested.raw_text}\n--- END DOCUMENT ---"
        parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
        if not result.ok or parsed is None:
            warnings.append(f"{doc_path.name}: KB compile LLM call unavailable ({result.error})")
            continue

        not_stored: Counter[str] = Counter()
        for fact in parsed if isinstance(parsed, list) else []:
            claim = str(fact.get("claim", "")).strip()
            if not claim or claim.lower() in existing_claims:
                continue
            kind = str(fact.get("kind", "claim")).strip().lower()
            kind = kind if kind in KB_KINDS else "claim"  # unknown label: a human decides
            if kind in _NEVER_STORED:
                not_stored[kind] += 1
                continue
            new_entries.append(
                KBEntry(
                    id=f"kb-{uuid.uuid4().hex[:8]}",
                    claim=claim,
                    source_doc=doc_path.name,
                    location=fact.get("location"),
                    status="pending" if kind == "claim" else "excluded",
                    kind=kind,
                    exclusion_reason=None if kind == "claim" else KB_KINDS[kind],
                )
            )
            existing_claims.add(claim.lower())
        if not_stored:
            skipped = ", ".join(f"{n} {k}" for k, n in sorted(not_stored.items()))
            warnings.append(f"{doc_path.name}: not stored ({skipped})")

    all_entries = existing + new_entries
    _save_index(client_dir, all_entries)
    return all_entries, warnings
