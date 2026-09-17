"""Do-not-say / compliance gate, sourced from a client's constraints.yaml (ClientProfile.do_not_say).
Deterministic and per-client; zero tolerance, same fail-the-run behavior as style_lint."""
from __future__ import annotations

from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus


def run(draft: Draft, profile: ClientProfile) -> GateResult:
    lowered = draft.body.lower()
    hits = [term for term in profile.do_not_say if term.lower() in lowered]
    if hits:
        return GateResult(
            gate_name="client_constraints",
            status=GateStatus.FAILED,
            detail=f"{len(hits)} constraint violation(s)",
            flagged_items=[f"do-not-say term present: {h!r}" for h in hits],
        )
    return GateResult(gate_name="client_constraints", status=GateStatus.PASSED, detail="clean")
