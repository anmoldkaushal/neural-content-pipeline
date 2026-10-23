"""Keeps a brief's conditional must-follow rules to the pieces they're about. A client's standing
instructions mix rules for every piece ("the call to action is 'Apply for a discovery call'") with
rules for one kind of piece ("medicine articles need a safety note, a risks section, a legal note
and a newsletter call to action"). Every line reached every job: a guide to the Egypt journey was
given the medicine-article rule, its angle grew a sacred medicine section with risks, legal status
and a newsletter prompt, and the voice judge failed it three times for reading like a medicine
article.

One cheap call at the start of a job, before preflight and the angle menu, reads each must-follow
line against the piece (goal, format, plan item, a sequence's steps): a rule for every piece is
kept, a rule for a kind of piece is kept only when this piece is that kind. A passing mention
doesn't make it that kind: a journey guide that mentions sacred medicine once isn't a medicine
article. Fails closed: if the call is unavailable or a line's verdict is unclear, the line is kept,
which is how every job ran before. What was left out, and why, goes on the job record."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Brief

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "rule_scope.md"


def lines(notes: Optional[str]) -> list[str]:
    return [line.strip() for line in (notes or "").splitlines() if line.strip()]


def scope(
    brief: Brief,
    piece: str = "",
    transport: Optional[ClaudeTransport] = None,
) -> tuple[Optional[str], list[str], Optional[str]]:
    """(notes to apply, rules left out with why, a note when the check couldn't run). `piece`: the
    plan item(s) the brief writes, as the prompt blocks content_plan renders."""
    rules = lines(brief.notes)
    if not rules:
        return brief.notes, [], None
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        f"{template}\n\nTHE PIECE\nGoal: {brief.goal}\nFormat: {brief.format}\n"
        + (f"Sequence of {brief.sequence_length} emails\n" if brief.sequence_length else "")
        + (f"{piece.strip()}\n" if piece.strip() else "")
        + "\nRULES\n" + "".join(f"{i}. {r}\n" for i, r in enumerate(rules, 1))
    )
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    verdicts = parsed.get("rules") if isinstance(parsed, dict) else None
    if not result.ok or not isinstance(verdicts, list):
        return brief.notes, [], f"rule scoping unavailable, every must-follow rule applied: {result.error or 'no verdicts'}"

    left_out: dict[int, str] = {}
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        try:
            n = int(v.get("n"))
        except (TypeError, ValueError):
            continue
        # Only a rule the judge both calls conditional and says doesn't apply is left out.
        if 1 <= n <= len(rules) and str(v.get("scope")).lower() == "conditional" and v.get("applies") is False:
            left_out[n] = str(v.get("why") or "not this kind of piece").strip()
    kept = [r for i, r in enumerate(rules, 1) if i not in left_out]
    return "\n".join(kept) or None, [f"{rules[n - 1]} ({why})" for n, why in sorted(left_out.items())], None
