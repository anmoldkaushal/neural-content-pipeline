"""Ingests every document dropped in a client's knowledge_base/documents/ folder into the three
layers a job reads:

- facts: kb_index.json entries, drafted here and confirmed by a human in kb_verify.py. Never
  overwrites an already-verified entry silently -- a conflicting new claim becomes its own pending
  entry, not an automatic replacement.
- context: the document itself, split into sections (pipeline/kb/context.py), with a role and a
  one-paragraph summary, so the writer can read it for direction.
- plan: any planned pieces the document lays out (pipeline/kb/content_plan.py), proposed for a
  human to approve.

Facts are extracted chunk by chunk (a few pages at a time) rather than in one pass over the whole
document: a single pass capped at 25 statements stopped partway through a 26-page strategy and
never reached its later sections."""
from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Optional

from pipeline.ingest import ingest_document
from pipeline.kb import content_plan, context
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import CONTEXT_ROLES, KB_KINDS, ContextDoc, IngestedDocument, KBEntry

# Never written to the index: project notes aren't copy material, and personal details about
# named private individuals shouldn't sit in a client's knowledge base at all.
_NEVER_STORED = {"internal", "personal"}

_PROMPTS = Path(__file__).resolve().parent.parent / "llm" / "prompts"
_PROMPT_PATH = _PROMPTS / "kb_compile.md"
_SURVEY_PROMPT_PATH = _PROMPTS / "doc_survey.md"

CHUNK_CHARS = 12000  # per fact-extraction call; a few pages of a typical deck or doc
SURVEY_CHARS = 60000  # the survey reads the whole document up to this


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


def chunks(doc: IngestedDocument, limit: int = CHUNK_CHARS) -> list[tuple[str, str]]:
    """(label, text) pieces of at most about `limit` characters, on page boundaries when the
    document has pages ("pages 4-6"), else on paragraph boundaries ("part 2")."""
    if doc.pages:
        units = [(f"page {n}", t) for n, t in enumerate(doc.pages, start=1) if t.strip()]
    else:
        units = [(f"part {n}", s.text) for n, s in enumerate(context.split_sections(doc), start=1)]

    out: list[tuple[str, str]] = []
    group: list[tuple[str, str]] = []
    for unit in units:
        if group and sum(len(t) for _, t in group) + len(unit[1]) > limit:
            out.append(_label(group))
            group = []
        group.append(unit)
    if group:
        out.append(_label(group))
    return out


def _label(group: list[tuple[str, str]]) -> tuple[str, str]:
    first, last = group[0][0], group[-1][0]
    if first == last:
        label = first
    else:
        label = f"{first.split()[0]}s {first.split()[-1]}-{last.split()[-1]}"
    text = "\n\n".join(f"[{name}]\n{t}" for name, t in group)
    return label, text


def survey(doc: IngestedDocument, name: str, transport: ClaudeTransport) -> tuple[Optional[dict], Optional[str]]:
    """One call per document: its role, a summary, and any planned pieces it lays out."""
    template = _SURVEY_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = f"{template}\n\n--- DOCUMENT: {name} ---\n{doc.raw_text[:SURVEY_CHARS]}\n--- END DOCUMENT ---"
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, dict):
        return None, result.error or "survey reply was not a JSON object"
    return parsed, None


def compile_kb(
    client_dir: Path, transport: Optional[ClaudeTransport] = None
) -> tuple[list[KBEntry], list[str]]:
    """Returns (all_entries_after_compile, warnings). Only statements labelled "claim" land as
    'pending' for review; style/audience/reference context is kept as 'excluded' with a reason,
    and internal or personal statements are not stored at all (reported in warnings). Each
    document is also saved as readable context, and its planned pieces proposed to the plan."""
    transport = transport or ClaudeTransport()
    existing = _load_index(client_dir)
    existing_claims = {e.claim.strip().lower() for e in existing}
    warnings: list[str] = []

    docs_dir = client_dir / "knowledge_base" / "documents"
    doc_paths = (
        sorted(p for p in docs_dir.iterdir() if p.is_file() and not p.name.startswith("."))
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

        surveyed, error = survey(ingested, doc_path.name, transport)
        if error:
            warnings.append(f"{doc_path.name}: document survey unavailable ({error}); kept as context without a role")
        surveyed = surveyed or {}
        role = str(surveyed.get("role", "other")).strip().lower()
        context.save_doc(client_dir, ContextDoc(
            name=doc_path.name,
            role=role if role in CONTEXT_ROLES else "other",
            summary=str(surveyed.get("summary", "")).strip(),
            sections=context.split_sections(ingested),
        ))
        plan_items = surveyed.get("plan_items") if isinstance(surveyed.get("plan_items"), list) else []
        added = content_plan.merge_proposed(client_dir, [p for p in plan_items if isinstance(p, dict)], doc_path.name)
        if added:
            warnings.append(f"{doc_path.name}: proposed {len(added)} content plan item(s) for review")

        not_stored: Counter[str] = Counter()
        for label, text in chunks(ingested):
            prompt = (
                f"{template}\n\n--- DOCUMENT: {doc_path.name} ({label}) ---\n{text}\n--- END DOCUMENT ---"
            )
            parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
            if not result.ok or parsed is None:
                warnings.append(f"{doc_path.name} ({label}): KB compile LLM call unavailable ({result.error})")
                continue

            for fact in parsed if isinstance(parsed, list) else []:
                claim = str(fact.get("claim", "")).strip()
                if not claim or claim.lower() in existing_claims:
                    continue
                kind = str(fact.get("kind", "claim")).strip().lower()
                kind = kind if kind in KB_KINDS else "claim"  # unknown label: a human decides
                if kind in _NEVER_STORED:
                    not_stored[kind] += 1
                    continue
                location = fact.get("location")
                new_entries.append(
                    KBEntry(
                        id=f"kb-{uuid.uuid4().hex[:8]}",
                        claim=claim,
                        source_doc=doc_path.name,
                        location=f"{label}: {location}" if location else label,
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
