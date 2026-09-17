"""LLM voice/editorial critic: an independent pass over the draft, never the same call that
produced it, to avoid self-grading bias (see llm_judge.py's reasoning in longevity-science-daily).
Degrades honestly: if the transport is unavailable, the gate reports SKIPPED, never PASSED."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "voice_critic.md"


def run(
    draft: Draft, profile: ClientProfile, transport: Optional[ClaudeTransport] = None
) -> GateResult:
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")

    style_guide_summary = ", ".join(t.name for t in profile.tone_presets) or "no tone presets defined"
    user_prompt = (
        f"{template}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n"
        f"Approved tone presets: {style_guide_summary}\n\n"
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
