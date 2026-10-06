"""Shared verdict logic for the judged (LLM) gates. A judge asked to critique copy will nearly always
find something, so each note carries a severity and only a "blocking" note fails the gate; "minor"
notes ride along on a passing result for the human. Fails closed: a note with no severity counts as
blocking, and a non-passing verdict with no notes at all is still a failure."""
from __future__ import annotations

from typing import Any

from pipeline.schemas import GateResult, GateStatus

SEVERITY_FORMAT = (
    'Each note is an object: {"severity": "blocking" or "minor", "note": "..."}. Mark a note blocking '
    "only if the piece should not go to a reader with it; everything else is minor. Name the exact "
    "sentence or pattern, not a vague impression."
)


def _note(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        severity = str(item.get("severity", "blocking")).strip().lower()
        return ("minor" if severity == "minor" else "blocking"), str(item.get("note", "")).strip()
    return "blocking", str(item).strip()


def verdict(gate_name: str, parsed: dict, pass_verdict: str, pass_detail: str, fail_detail: str) -> GateResult:
    notes = [_note(n) for n in parsed.get("notes") or []]
    blocking = [text for severity, text in notes if severity == "blocking" and text]
    minor = [text for severity, text in notes if severity == "minor" and text]
    if blocking or (parsed.get("verdict") != pass_verdict and not minor):
        return GateResult(gate_name=gate_name, status=GateStatus.FAILED, detail=fail_detail,
                          flagged_items=blocking or ["judge returned a failing verdict with no reason"], notes=minor)
    detail = pass_detail + (f", {len(minor)} minor note(s) for review" if minor else "")
    return GateResult(gate_name=gate_name, status=GateStatus.PASSED, detail=detail, notes=minor)
