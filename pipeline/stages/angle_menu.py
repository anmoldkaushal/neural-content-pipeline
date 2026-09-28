"""Cheap, pre-drafting angle selection: 2-3 short candidates, not full drafts, so a bad angle
never costs a full draft-and-gate cycle."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.kb.approved_examples import format_example_block
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Angle, Brief, KBEntry

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "angle_menu.md"


def _steering(brief: Brief) -> str:
    lines = []
    if brief.angle_hint:
        lines.append(f"Angle hint: {brief.angle_hint}")
    if brief.notes:
        lines.append(f"Must follow: {brief.notes}")
    return "".join(line + "\n" for line in lines)


def generate_angles(
    brief: Brief,
    kb_entries: list[KBEntry],
    transport: Optional[ClaudeTransport] = None,
    examples: Optional[list[str]] = None,
) -> list[Angle]:
    transport = transport or ClaudeTransport()
    verified = [e for e in kb_entries if e.status == "verified"]
    facts_block = "\n".join(f"- [{e.id}] {e.claim}" for e in verified)

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        f"{template}\n\nBrief goal: {brief.goal}\nAudience: {brief.audience}\n"
        f"Format: {brief.format}\n{_steering(brief)}{format_example_block(examples or [])}"
        f"\nVerified facts:\n{facts_block or '(none)'}"
    )

    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, list):
        return []

    angles: list[Angle] = []
    for item in parsed[:3]:
        try:
            angles.append(Angle(**item))
        except Exception:  # noqa: BLE001 - skip a malformed candidate, don't fail the whole menu
            continue
    return angles
