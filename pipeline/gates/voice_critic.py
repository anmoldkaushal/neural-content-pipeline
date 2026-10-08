"""LLM voice/editorial critic: an independent pass over the draft, never the same call that
produced it, to avoid self-grading bias (see llm_judge.py's reasoning in longevity-science-daily).
Degrades honestly: if the transport is unavailable, the gate reports SKIPPED, never PASSED."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.gates import judged
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
    must_follow: Optional[str] = None,
    reader_profile: Optional[str] = None,
    facts: str = "",
    earlier_notes: Optional[list[str]] = None,
    sequence_context: str = "",
) -> GateResult:
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")

    # The same style guide and full preset descriptions the writer was given, so the judge's notes
    # measure the draft against what the writer was actually asked for, not against preset names.
    style_guide = f"Client style guide:\n{profile.style_guide.strip()}\n\n" if profile.style_guide.strip() else ""
    # What the writer was not allowed to do, so the judge doesn't demand it (e.g. a named deal or a
    # figure the framing rules forbid): the voice is judged within those limits, not against them.
    limits = "".join(f"- {rule}\n" for rule in profile.do_not_frame)
    if must_follow:
        limits += f"- Brief must-follow: {must_follow}\n"
    user_prompt = (
        f"{template}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n"
        f"Approved tone presets:\n{rulebook.tone_lines(profile)}\n"
        f"{_chosen_tone_line(tone)}\n"
        + (f"{reader_profile}\n" if reader_profile else "")
        + f"{style_guide}"
        f"{judged.SEVERITY_FORMAT}\n"
        + (f"Limits the writer had to work within:\n{limits}" if limits else "")
        # The same banned terms and verified facts every judge gets (judged.shared_rules): without the
        # facts it failed confirmed facts as unbacked; without the terms it told the writer to add
        # "not a conference or a networking event", both do-not-say terms.
        + f"\n{judged.shared_rules(profile, facts)}"
        # What this judge asked for in earlier rounds, so it can't ask for the opposite now.
        + ("\nYour notes on earlier drafts of this piece (the writer has acted on them; don't reverse "
           "them):\n" + "".join(f"- {n}\n" for n in earlier_notes) if earlier_notes else "")
        + sequence_context
        + "\n"
        f"--- DRAFT ---\n{draft.body}\n--- END DRAFT ---"
    )

    parsed, result = call_json(transport, system_prompt="", user_prompt=user_prompt)
    if not result.ok or parsed is None:
        return GateResult(
            gate_name="voice_critic",
            status=GateStatus.SKIPPED,
            detail=f"judge unavailable: {result.error or 'no error detail'}",
        )

    if not isinstance(parsed, dict):
        parsed = {"verdict": None, "notes": []}
    return judged.verdict("voice_critic", parsed, "on_voice", "on voice", "judge flagged blocking voice issues")
