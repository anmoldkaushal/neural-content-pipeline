"""The client's rules as one prompt block: style guide, tone, banned words and phrases, do-not-say
terms, framing rules and length. Before this, the drafter never saw any of them -- only the gates
did -- so a draft could only learn a rule by failing it. Built from the same ClientProfile and
lists the gates check, so what the writer is told and what it is judged on cannot drift apart."""
from __future__ import annotations

from typing import Optional

from pipeline.gates.style_lint import banned_lists
from pipeline.schemas import ClientProfile, ToneChoice, WordRange


def render(
    profile: ClientProfile,
    tone: Optional[ToneChoice] = None,
    word_range: Optional[WordRange] = None,
) -> str:
    banned_words, banned_phrases = banned_lists(profile)
    lines = [f"CLIENT RULES for {profile.company_name or profile.client_id} (every draft is checked against these)"]

    if profile.style_guide.strip():
        lines += ["", "Style guide:", profile.style_guide.strip()]

    if tone is not None:
        lines += ["", f"Tone for this piece: {tone.preset_name}: {tone.resolved_style_checklist.get('tone_description', '')}"]
        sample = tone.resolved_style_checklist.get("sample_line")
        if sample:
            lines.append(f"A line in this tone: {sample}")

    lines += [
        "",
        "Never use these words (checked by exact match): " + ", ".join(banned_words),
        "Never use these phrases: " + "; ".join(banned_phrases),
        "Never use an em dash or en dash; use commas, colons, full stops or parentheses.",
    ]
    if profile.do_not_say:
        lines.append("Never say (client terms, exact match): " + "; ".join(profile.do_not_say))
    if profile.do_not_frame:
        lines += ["", "Framing rules (an independent reviewer reads the draft's meaning against each):"]
        lines += [f"- {rule}" for rule in profile.do_not_frame]
    if word_range is not None:
        lines += ["", f"Length: {word_range.label()} for the body."]
    return "\n".join(lines) + "\n"


def tone_lines(profile: ClientProfile) -> str:
    """Every approved preset with its description and sample, for the voice judge, which used to
    get only the preset names and so judged against a label rather than a description."""
    if not profile.tone_presets:
        return "no tone presets defined"
    return "\n".join(f"- {t.name}: {t.description} (e.g. \"{t.sample_line}\")" for t in profile.tone_presets)
