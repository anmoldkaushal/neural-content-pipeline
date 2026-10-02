"""LLM voice/editorial critic: an independent pass over the draft, never the same call that
produced it, to avoid self-grading bias (see llm_judge.py's reasoning in longevity-science-daily).
Degrades honestly: if the transport is unavailable, the gate reports SKIPPED, never PASSED."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.kb import rulebook
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus, ToneChoice

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "voice_critic.md"


def _chosen_tone_line(tone: Optional[ToneChoice]) -> str:
    if tone is None:
        return ""
    line = f"Tone chosen for this piece: {tone.preset_name} — {tone.resolved_style_checklist.get('tone_description', '')}"
    if tone.ad_hoc:
        line += " (a one-off tone a human chose for this job, not a preset; judge against it)"
    return line + "\n"


def run(
    draft: Draft,
    profile: ClientProfile,
    transport: Optional[ClaudeTransport] = None,
    tone: Optional[ToneChoice] = None,
) -> GateResult:
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")

    # The same style guide and full preset descriptions the writer was given, so the judge's notes
    # measure the draft against what the writer was actually asked for, not against preset names.
    style_guide = f"Client style guide:\n{profile.style_guide.strip()}\n\n" if profile.style_guide.strip() else ""
    user_prompt = (
        f"{template}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n"
        f"Approved tone presets:\n{rulebook.tone_lines(profile)}\n"
        f"{_chosen_tone_line(tone)}\n"
        f"{style_guide}"
        f"--- DRAFT ---\n{draft.body}\n--- END DRAFT ---"
    )

    parsed, result = call_json(transport, system_prompt="", user_prompt=user_prompt)
    if not result.ok or parsed is None:
        return GateResult(
            gate_name="voice_critic",
            status=GateStatus.SKIPPED,
            detail=f"judge unavailable: {result.error or 'no error detail'}",
        )

    verdict = parsed.get("verdict") if isinstance(parsed, dict) else None
    notes = parsed.get("notes", []) if isinstance(parsed, dict) else []

    if verdict == "on_voice":
        return GateResult(gate_name="voice_critic", status=GateStatus.PASSED, detail="on voice")
    return GateResult(
        gate_name="voice_critic",
        status=GateStatus.FAILED,
        detail="judge flagged voice issues",
        flagged_items=list(notes),
    )
