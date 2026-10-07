"""Agent review of a client's knowledge base, so a person checks only what's risky.

A compile pulls every statement out of every document, which for a client with a stack of call
notes means hundreds of near-identical facts. This stage runs after compile, over facts nobody has
decided on yet:

1. Duplicates: one call groups facts that say the same thing (including ones already confirmed)
   under one wording, keeping every source; it also names facts that contradict each other.
2. Check against source: per document, an independent call re-reads each fact next to the
   document text: is it about the client, does the document support it, how risky is it. The
   quote it gives is then matched against the document text in code, as are any figures in the
   fact, so a misread can't pass on the model's word alone.
3. Decide (decide(), deterministic): a supported, uncontradicted, low-risk fact is confirmed by the
   agent (confirmed_by AGENT in provenance.json, so it stays visible as the agent's call); one the
   document doesn't support is rejected; one that isn't about the client is set aside; anything
   conflicting, high-stakes, informal or not cleanly matched goes to "needs you", ranked, and the
   UI shows the top few.

The drafting gates still check every draft against the facts (entailment, claims_critic), and the
package marks facts the agent confirmed. undo() puts every agent decision back to pending."""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from pipeline.kb import context
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import KB_KINDS, KBEntry
from pipeline.stages import kb_verify

AGENT = "agent:kb-review"
NEEDS_YOU_SHOWN = 10  # the UI shows this many of the ranked "needs you" queue at a time
ASSESS_BATCH = 25  # facts per check-against-source call
DOC_CHARS = 90000  # document text sent with each check call
CALL_TIMEOUT = 600  # seconds; these calls carry a whole document or the whole fact list

_PROMPTS = Path(__file__).resolve().parent.parent / "llm" / "prompts"
_DEDUPE_PROMPT = _PROMPTS / "kb_dedupe.md"
_ASSESS_PROMPT = _PROMPTS / "kb_assess.md"
_HIGH_STAKES = {"price", "date", "number", "guarantee", "outcome", "named_person", "named_customer"}
_NOT_CLIENT = {"audience", "reference", "internal", "personal"}


# ------------------------------------------------------------------ text matching


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def quote_found(quote: str, source: str) -> bool:
    """The model's quote really is in the document (ignoring case, spacing and curly quotes)."""
    q = _norm(quote).strip(" .\"'")
    return len(q) >= 8 and q in _norm(source)


def missing_figures(claim: str, source: str) -> list[str]:
    """Figures the fact states (150, 48, 9th, 25,000) that appear nowhere in the source text."""
    src = _norm(source).replace(",", "")
    out = []
    for fig in re.findall(r"\d[\d,]*(?:\.\d+)?", claim):
        bare = fig.replace(",", "")
        if not re.search(rf"(?<![\d.]){re.escape(bare)}(?![\d])", src):
            out.append(fig)
    return out


# ------------------------------------------------------------------ decision


def decide(
    assessment: dict[str, Any],
    claim: str,
    sources_text: str,
    n_sources: int,
    formal_source: bool,
    conflict: Optional[str],
) -> tuple[str, str, int]:
    """(triage, reason, priority) for one fact. triage is "auto_verified", "needs_you",
    "unsupported" or "not_client_fact:<kind>". Pure: the policy, in one place."""
    if assessment.get("client_fact") is False:
        kind = str(assessment.get("kind_if_not") or "reference").strip().lower()
        kind = kind if kind in _NOT_CLIENT else "reference"
        return f"not_client_fact:{kind}", assessment.get("note") or KB_KINDS[kind], 0

    supported = str(assessment.get("supported") or "").strip().lower()
    stakes = sorted({str(s).strip().lower() for s in assessment.get("high_stakes") or []} & _HIGH_STAKES)
    informal = bool(assessment.get("informal")) or not formal_source
    try:
        value = min(max(int(assessment.get("copy_value") or 2), 1), 3)
    except (TypeError, ValueError):
        value = 2
    note = str(assessment.get("note") or "").strip()

    if supported == "no":
        return "unsupported", note or "the source document doesn't say this", 0

    reasons: list[str] = []
    priority = value * 10
    if conflict:
        reasons.append(f"conflicts with another fact: {conflict}")
        priority += 100
    quote = str(assessment.get("quote") or "")
    if not quote_found(quote, sources_text):
        reasons.append("couldn't find the supporting passage word for word in the source")
        priority += 20
    if missing := missing_figures(claim, sources_text):
        reasons.append(f"figure(s) not in the source: {', '.join(missing)}")
        priority += 40
    if supported != "yes":
        reasons.append("the source only partly supports it" + (f": {note}" if note else ""))
        priority += 20
    corroborated = n_sources >= 2 and formal_source
    if stakes and not corroborated:
        reasons.append(f"states a {', '.join(stakes).replace('_', ' ')} from a single source")
        priority += 30
    if informal and not corroborated:
        reasons.append("rests on informal notes or a remark, not a settled document")
        priority += 10

    if reasons:
        return "needs_you", "; ".join(reasons), priority
    return "auto_verified", note or "stated plainly in the source; low risk", 0


# ------------------------------------------------------------------ calls


def _with_timeout(transport: ClaudeTransport) -> ClaudeTransport:
    if hasattr(transport, "timeout"):
        transport.timeout = max(int(transport.timeout), CALL_TIMEOUT)
    return transport


def dedupe(
    queue: list[KBEntry], confirmed: list[KBEntry], transport: ClaudeTransport
) -> tuple[list[dict], list[dict], Optional[str]]:
    lines = ["CONFIRMED:"] + ([f"[{e.id}] {e.claim} ({e.source_doc})" for e in confirmed] or ["(none)"])
    lines += ["", "NEW:"] + [f"[{e.id}] {e.claim} ({e.source_doc})" for e in queue]
    prompt = f"{_DEDUPE_PROMPT.read_text(encoding='utf-8')}\n\n" + "\n".join(lines)
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, dict):
        return [], [], result.error or "duplicate check reply was not a JSON object"
    dups = [d for d in parsed.get("duplicates") or [] if isinstance(d, dict)]
    conflicts = [c for c in parsed.get("conflicts") or [] if isinstance(c, dict)]
    return dups, conflicts, None


def assess(
    doc_name: str, doc_text: str, facts: list[KBEntry], transport: ClaudeTransport
) -> tuple[dict[str, dict], list[str]]:
    template = _ASSESS_PROMPT.read_text(encoding="utf-8")
    out: dict[str, dict] = {}
    errors: list[str] = []
    for start in range(0, len(facts), ASSESS_BATCH):
        batch = facts[start:start + ASSESS_BATCH]
        listing = "\n".join(f"[{e.id}] {e.claim}" for e in batch)
        prompt = (f"{template}\n\nFACTS:\n{listing}\n\n--- DOCUMENT: {doc_name} ---\n"
                  f"{doc_text[:DOC_CHARS]}\n--- END DOCUMENT ---")
        parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
        if not result.ok or not isinstance(parsed, list):
            errors.append(f"{doc_name}: check unavailable ({result.error or 'reply was not a JSON array'})")
            continue
        out.update({str(a.get("id")): a for a in parsed if isinstance(a, dict) and a.get("id")})
    return out, errors


# ------------------------------------------------------------------ the run


def _load(client_dir: Path) -> list[KBEntry]:
    path = client_dir / "knowledge_base" / "kb_index.json"
    return [KBEntry(**e) for e in json.loads(path.read_text(encoding="utf-8"))] if path.exists() else []


def _save(client_dir: Path, entries: list[KBEntry]) -> None:
    path = client_dir / "knowledge_base" / "kb_index.json"
    path.write_text(json.dumps([e.model_dump(mode="json") for e in entries], indent=2), encoding="utf-8")


def _doc_texts(client_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Document text and role by file name, from the context layer compile already saved."""
    texts, roles = {}, {}
    for d in context.load_docs(client_dir):
        texts[d.name] = "\n\n".join(s.text for s in d.sections)
        roles[d.name] = d.role
    return texts, roles


def run(client_dir: Path, transport: Optional[ClaudeTransport] = None) -> dict[str, Any]:
    """Reviews every pending fact the agent hasn't seen yet. Returns counts plus any warnings."""
    entries = _load(client_dir)
    by_id = {e.id: e for e in entries}
    queue = [e for e in entries if e.status == "pending" and e.kind == "claim" and e.triage is None]
    summary: dict[str, Any] = {"reviewed": len(queue), "auto_verified": 0, "needs_you": 0, "duplicate": 0,
                               "unsupported": 0, "not_client_fact": 0, "warnings": []}
    if not queue:
        return summary
    transport = _with_timeout(transport or ClaudeTransport())
    in_queue = {e.id for e in queue}
    confirmed = [e for e in entries if e.status == "verified"]

    # 1. duplicates and conflicts
    dups, conflicts, error = dedupe(queue, confirmed, transport)
    if error:
        summary["warnings"].append(f"duplicate check skipped: {error}")
    merged_into: dict[str, str] = {}
    for group in dups:
        keep = str(group.get("keep") or "")
        same = [str(i) for i in group.get("same") or [] if str(i) in in_queue and str(i) != keep]
        if keep not in by_id or not same or by_id[keep].status not in ("pending", "verified") or keep in merged_into:
            continue
        kept = by_id[keep]
        wording = str(group.get("claim") or "").strip()
        if wording and kept.status == "pending" and wording != kept.claim:
            kept.original_claim = kept.original_claim or kept.claim
            kept.claim = wording
        for i in same:
            if i in merged_into:
                continue
            dup = by_id[i]
            merged_into[i] = keep
            dup.status, dup.kind, dup.superseded_by = "excluded", "duplicate", keep
            dup.triage, dup.triage_reason = "duplicate", f"same fact as {keep}"
            dup.exclusion_reason = f"duplicate of {keep}: {kept.claim[:80]}"
            where = f"{dup.source_doc}" + (f" · {dup.location}" if dup.location else "")
            if where not in kept.also_sources:
                kept.also_sources.append(where)
            summary["duplicate"] += 1
    conflict_of: dict[str, str] = {}
    for c in conflicts:
        ids = {merged_into.get(str(i), str(i)) for i in c.get("ids") or []}
        if len(ids & set(by_id)) >= 2:
            for i in ids:
                conflict_of[i] = str(c.get("issue") or "the documents disagree")

    # 2. check each surviving fact against its own document
    survivors = [e for e in queue if e.id not in merged_into]
    texts, roles = _doc_texts(client_dir)
    by_doc: dict[str, list[KBEntry]] = defaultdict(list)
    for e in survivors:
        by_doc[e.source_doc].append(e)
    assessments: dict[str, dict] = {}
    for doc, facts in by_doc.items():
        if doc not in texts:
            summary["warnings"].append(f"{doc}: no compiled text to check against; left for review")
            continue
        found, errors = assess(doc, texts[doc], facts, transport)
        assessments.update(found)
        summary["warnings"] += errors

    # 3. decide and apply
    sources_of = {e.id: [e.source_doc] + [s.split(" · ")[0] for s in e.also_sources] for e in survivors}
    auto, exclude_reasons, purge_ids = [], {}, []
    for e in survivors:
        a = assessments.get(e.id)
        if a is None:
            e.triage, e.triage_reason, e.triage_priority = "needs_you", "the agent couldn't check this one", 50
            summary["needs_you"] += 1
            continue
        docs = list(dict.fromkeys(sources_of[e.id]))
        sources_text = "\n\n".join(texts.get(d, "") for d in docs)
        formal = any(roles.get(d, "other") != "notes" for d in docs)
        triage, reason, priority = decide(a, e.claim, sources_text, len(docs), formal, conflict_of.get(e.id))
        quote = str(a.get("quote") or "").strip()
        e.evidence = quote[:500] or None
        if triage.startswith("not_client_fact"):
            kind = triage.split(":", 1)[1]
            e.triage, e.triage_reason = "not_client_fact", reason
            if kind == "personal":
                purge_ids.append(e.id)
            else:
                exclude_reasons[e.id] = (kind, f"agent: {reason}")
            summary["not_client_fact"] += 1
            continue
        e.triage, e.triage_reason, e.triage_priority = triage, reason, priority or None
        if triage == "auto_verified":
            auto.append(e)
        elif triage == "unsupported":
            e.status, e.verified_at, e.confirmed_by = "rejected", dt.date.today(), AGENT
        summary[triage] += 1

    for e in auto:
        e.status, e.verified_at, e.confirmed_by = "verified", dt.date.today(), AGENT
    for i, (kind, reason) in exclude_reasons.items():
        by_id[i].status, by_id[i].kind, by_id[i].exclusion_reason = "excluded", kind, reason
    _save(client_dir, [e for e in entries if e.id not in set(purge_ids)])
    kb_verify.stamp_provenance(client_dir, auto, AGENT)
    return summary


# ------------------------------------------------------------------ reading and undoing


def needs_you(client_dir: Path) -> list[KBEntry]:
    """Facts waiting for a person, most important first."""
    waiting = [e for e in _load(client_dir) if e.status == "pending" and e.triage == "needs_you"]
    return sorted(waiting, key=lambda e: -(e.triage_priority or 0))


def unreviewed(client_dir: Path) -> int:
    return sum(1 for e in _load(client_dir) if e.status == "pending" and e.kind == "claim" and e.triage is None)


def edit_and_confirm(client_dir: Path, entry_id: str, claim: str, confirmed_by: str) -> None:
    """A person rewords a fact the agent flagged and confirms their wording."""
    claim = claim.strip()
    if not claim:
        raise ValueError("a fact can't be empty")
    entries = _load(client_dir)
    for e in entries:
        if e.id == entry_id and e.claim != claim:
            e.original_claim = e.original_claim or e.claim
            e.claim = claim
    _save(client_dir, entries)
    kb_verify.approve(client_dir, [entry_id], confirmed_by)


def undo(client_dir: Path) -> int:
    """Puts every agent decision back to pending (and unreviewed), restoring merged duplicates and
    original wordings and withdrawing the agent's confirmations. Decisions a person made since are
    kept. Facts the agent dropped as personal details aren't kept on disk, so they can't return."""
    entries = _load(client_dir)
    agent_confirmed: set[str] = set()
    n = 0
    for e in entries:
        human_decided = e.confirmed_by not in (None, AGENT)
        if e.triage is None or human_decided:
            continue
        if e.status == "verified":
            agent_confirmed.add(e.claim.strip().lower())
        e.claim = e.original_claim or e.claim
        e.status, e.kind = "pending", "claim"
        e.exclusion_reason = e.superseded_by = e.evidence = e.original_claim = None
        e.triage = e.triage_reason = e.triage_priority = None
        e.verified_at = e.confirmed_by = None
        e.also_sources = []
        n += 1
    _save(client_dir, entries)
    kb_verify._withdraw_provenance(client_dir, agent_confirmed)
    return n
