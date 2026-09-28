"""Deterministic gate for the micro-copy a human picked off the menu: the same banned-word/phrase,
dash, and do-not-say rules the body is held to (style_lint + client_constraints), minus the word
count. Runs before drafting: a redraft of the body can never fix a bad subject line, so a failure
here blocks the job instead of feeding the revise loop."""
from __future__ import annotations

from pipeline.gates import client_constraints, style_lint
from pipeline.schemas import ClientProfile, GateResult, GateStatus


def flags_for(text: str, profile: ClientProfile) -> list[str]:
    return style_lint.find_violations(text, profile) + [
        f"do-not-say term present: {h!r}" for h in client_constraints.find_hits(text, profile)
    ]


def run(selected: dict[str, str], profile: ClientProfile) -> GateResult:
    flagged = [f"{field}: {flag}" for field, text in selected.items() for flag in flags_for(text, profile)]
    if flagged:
        return GateResult(
            gate_name="microcopy_lint",
            status=GateStatus.FAILED,
            detail=f"{len(flagged)} micro-copy violation(s)",
            flagged_items=flagged,
        )
    detail = f"{len(selected)} field(s) clean" if selected else "no micro-copy selected"
    return GateResult(gate_name="microcopy_lint", status=GateStatus.PASSED, detail=detail)
