"""Every load-bearing claim in a draft must trace to a verified KBEntry. Mirrors
longevity-science-daily's entailment_gate.py / tier1b_gate.py reasoning: an unsupported claim is
the single highest-cost failure mode for content built from a client's knowledge base, so this
gate is deterministic about WHICH claim ids are used (Draft.claims_used) and refuses on any id
that isn't a verified KB entry, rather than trying to re-derive entailment from prose alone."""
from __future__ import annotations

from pipeline.schemas import Draft, GateResult, GateStatus, KBEntry


def run(draft: Draft, kb_entries: list[KBEntry]) -> GateResult:
    verified_ids = {e.id for e in kb_entries if e.status == "verified"}
    unverified = [cid for cid in draft.claims_used if cid not in verified_ids]

    if unverified:
        return GateResult(
            gate_name="entailment",
            status=GateStatus.FAILED,
            detail=f"{len(unverified)} claim(s) not traceable to a verified KB entry",
            flagged_items=[f"unverified claim id: {c!r}" for c in unverified],
        )
    return GateResult(
        gate_name="entailment",
        status=GateStatus.PASSED,
        detail=f"all {len(draft.claims_used)} claim(s) trace to verified KB entries",
    )
