"""Compiles brief + chosen angle + chosen tone + verified KB into one working spec the drafter
follows. Keeps the drafter's context targeted instead of four raw input documents glommed together."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Angle, Brief, KBEntry, ToneChoice

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "brief_synthesis.md"


def synthesize(
    brief: Brief,
    angle: Angle,
    tone: ToneChoice,
    kb_entries: list[KBEntry],
    transport: Optional[ClaudeTransport] = None,
) -> dict[str, Any]:
    transport = transport or ClaudeTransport()
    verified_by_id = {e.id: e for e in kb_entries if e.status == "verified"}
    facts_block = "\n".join(
        f"- [{cid}] {verified_by_id[cid].claim}" for cid in angle.claims_used if cid in verified_by_id
    )

    notes_line = f"Must follow: {brief.notes}\n" if brief.notes else ""

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        f"{template}\n\nBrief: {brief.goal} (audience: {brief.audience}, format: {brief.format}, "
        f"target words: {brief.target_word_count or 'unspecified'})\n"
        f"{notes_line}\n"
        f"Chosen angle: {angle.headline} — {angle.pitch}\nStructure: {angle.structure}\n\n"
        f"Chosen tone: {tone.preset_name} — {tone.resolved_style_checklist.get('tone_description')}\n\n"
        f"Facts available:\n{facts_block or '(none)'}"
    )

    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, dict):
        # fail toward a minimal-but-usable spec rather than raising, so run_job.py can decide
        # whether to escalate; the pass-through claims_used makes downstream failures explicit.
        return {
            "outline": angle.structure,
            "style_checklist": {"tone": tone.preset_name},
            "claims_to_use": angle.claims_used,
            "synthesis_unavailable": True,
        }
    parsed.setdefault("claims_to_use", angle.claims_used)
    return parsed
