"""Judged gate: can a stranger tell, from the piece alone, who is writing, what they offer and what
they are asked to do? Every other judge reads with the brief and the verified facts, so it already
knows what the offering is and can't notice a piece that never says. Ten standalone cold emails
went out on 2026-10-20 calling the product "the desk" without saying what it was: the writer had
seen the earlier emails, and no judge read them as the recipient would. This one sees only the
text, never the brief, the facts or the client's name.

Same discipline as the other judges: an independent call, severity-tagged notes (judged.py), and
SKIPPED (never PASSED) when the transport is unavailable."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.gates import judged
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Draft, GateResult, GateStatus

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "cold_reader.md"


def run(draft: Draft, transport: Optional[ClaudeTransport] = None, audience: str = "") -> GateResult:
    """`audience`: the brief's reader, so the judge reads as one of them (knowing their own field's
    terms) rather than as a layperson. It says nothing about the sender or the offering."""
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    reader = (f"\nWho you are: {audience.strip()}. You know your own work and its terms; you know nothing "
              "about the sender.\n" if audience.strip() else "")
    user_prompt = (f"{template}{reader}\n{judged.SEVERITY_FORMAT}\n\n"
                   f"--- PIECE ---\n{draft.body}\n--- END PIECE ---")

    parsed, result = call_json(transport, system_prompt="", user_prompt=user_prompt)
    if not result.ok or parsed is None:
        return GateResult(gate_name="cold_reader", status=GateStatus.SKIPPED,
                          detail=f"judge unavailable: {result.error or 'no error detail'}")
    if not isinstance(parsed, dict):
        parsed = {"verdict": None, "notes": []}
    # A "can't tell" answer is a failure even if the judge forgot to say so in a note.
    blind = [f"a stranger can't tell {what} from this piece alone"
             for what, key in (("who is writing", "who"), ("what is offered", "offer"), ("what they are asked to do", "ask"))
             if "can't tell" in str(parsed.get(key, "")).lower()]
    if blind:
        parsed = {**parsed, "notes": list(parsed.get("notes") or []) + [{"severity": "blocking", "note": b} for b in blind]}
    return judged.verdict("cold_reader", parsed, "clear", "a stranger can follow it", "a stranger can't tell what this is")
