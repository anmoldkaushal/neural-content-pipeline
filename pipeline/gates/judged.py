"""Shared verdict logic for the judged (LLM) gates. A judge asked to critique copy will nearly always
find something, so each note carries a severity and only a "blocking" note fails the gate; "minor"
notes ride along on a passing result for the human. Fails closed: a note with no severity counts as
blocking, and a non-passing verdict with no notes at all is still a failure."""
from __future__ import annotations

from typing import Any

from pipeline.schemas import ClientProfile, GateResult, GateStatus

SEVERITY_FORMAT = (
    'Each note is an object: {"severity": "blocking" or "minor", "note": "..."}. Mark a note blocking '
    "only if the piece should not go to a reader with it; everything else is minor. Name the exact "
    "sentence or pattern, not a vague impression."
)


def banned_terms(profile: ClientProfile) -> list[str]:
    """Every term a draft may never contain: the client's do-not-say list and extra banned words
    and phrases (the house list is enforced by style_lint and already reaches the writer)."""
    return list(dict.fromkeys([*profile.do_not_say, *profile.banned_words, *profile.banned_phrases]))


def shared_rules(profile: ClientProfile, facts: str = "") -> str:
    """What every judge is told, so no two judges hold the writer to different rules: the words it
    may never use (so no judge suggests one as a fix) and the verified facts it may state (so no
    judge fails a confirmed fact as unconfirmed)."""
    out = ""
    if terms := banned_terms(profile):
        out += ("Terms the draft must never contain. Never suggest a fix that uses one, even to say what the "
                f"piece is not: {'; '.join(terms)}\n")
    if facts.strip():
        out += ("Verified facts the writer may state. A statement matching one is allowed and is not "
                "'unconfirmed', unless a rule forbids mentioning that topic at all:\n" + facts.strip() + "\n")
    return out


def sequence_block(piece: int, n: int, step: str, plan_block: str, sequence_notes: str,
                   earlier: list[str], judge_repeats: bool = True) -> str:
    """What a judge of one email in a sequence is told: this email's own job, what the sequence as a
    whole must do, and the emails already sent. Without it each email was judged against the whole
    campaign's must-follow, so every judge asked every email to open on the turning point, sell the
    room and end on the same call to action, and five emails came out as one email five times."""
    out = f"\nSEQUENCE: this draft is email {piece} of {n}, sent to the same reader after the earlier ones.\n"
    out += f"This email's job: {step}\n" + plan_block
    if sequence_notes:
        out += ("Across the whole sequence (the sequence as a whole meets these; this one email doesn't have "
                f"to): {sequence_notes}\n")
    if earlier:
        out += "Earlier emails in the sequence, already sent:\n" + "".join(f"--- {e}\n" for e in earlier)
    out += ("Judge this email against its own job. Never ask it to cover another email's job or to repeat "
            "what an earlier email already said.")
    if judge_repeats:  # the voice judge's call; the framing judge only reads rules
        out += " Re-covering an earlier email's point, or reusing its opening, call to action or phrasing, is blocking."
    return out + "\n"


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
