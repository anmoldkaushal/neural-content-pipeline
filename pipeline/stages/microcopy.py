"""Micro-copy menu: cheap, diverse candidates for the short copy fields of a content type
(subject line + preheader for an email, opener for a LinkedIn message, ...). Runs after the angle
is chosen and BEFORE drafting, so a human picks the copy first and the gates then check the picks
alongside the body. Field sets and counts per format live in config/pipeline_config.yaml.

generate_field distinguishes "the model ran and proposed nothing usable" from "the call failed" --
both come back as an empty candidate list, but only the latter carries an error string, so a
caller can tell a genuine empty result apart from a silent outage instead of treating both alike."""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from pipeline import config
from pipeline.gates import microcopy_lint
from pipeline.kb.approved_examples import format_example_block
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Angle, Brief, ClientProfile, MicrocopyCandidate, MicrocopyField, ToneChoice

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "microcopy.md"

_FALLBACK_CAPS: dict[MicrocopyField, int] = {
    MicrocopyField.TITLE: 3,
    MicrocopyField.SUBTITLE: 3,
    MicrocopyField.HOOK: 5,
    MicrocopyField.CTA: 5,
}


def generate_field(
    field: Union[MicrocopyField, str],
    angle: Angle,
    count: Optional[int] = None,
    transport: Optional[ClaudeTransport] = None,
    examples: Optional[list[str]] = None,
    context: str = "",
) -> tuple[list[MicrocopyCandidate], Optional[str]]:
    transport = transport or ClaudeTransport()
    field_name = field.value if isinstance(field, MicrocopyField) else str(field)
    count = count or config.microcopy_cap(field_name, _FALLBACK_CAPS.get(field_name, 3))

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    template = template.replace("{{FIELD_NAME}}", field_name).replace("{{COUNT}}", str(count))
    prompt = (
        f"{template}\n\n{context}Angle: {angle.headline} — {angle.pitch}"
        f"{format_example_block(examples or [])}"
    )

    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok:
        return [], result.error or "transport call failed"
    if not isinstance(parsed, list):
        return [], "model response was not a JSON array"

    candidates: list[MicrocopyCandidate] = []
    for item in parsed[:count]:
        text = str(item.get("text", "")).strip()
        strategy = str(item.get("strategy", "")).strip()
        if text:
            candidates.append(MicrocopyCandidate(field=field_name, text=text, strategy=strategy))
    if not candidates:
        return [], "model returned no usable candidates"
    return candidates, None


def menu_context(
    brief: Brief,
    tone: Optional[ToneChoice],
    plan_block: str = "",
    profile: Optional[ClientProfile] = None,
) -> str:
    """The brief, tone, plan item and framing rules every field prompt shares, so options fit the
    content type and don't frame the piece in a way the judged gates will reject."""
    content_type = f"{brief.format} ({brief.format_description})" if brief.format_description else brief.format
    lines = [f"Content type: {content_type}", f"Goal: {brief.goal}", f"Audience: {brief.audience}"]
    if tone:
        lines.append(f"Tone: {tone.preset_name} — {tone.resolved_style_checklist.get('tone_description', '')}")
    if brief.notes:
        lines.append(f"Must follow: {brief.notes}")
    if profile is not None and profile.do_not_frame:
        lines.append("Client framing rules (never break):")
        lines += [f"- {rule}" for rule in profile.do_not_frame]
    return "\n".join(lines) + "\n" + plan_block


def generate_all(
    angle: Angle,
    transport: Optional[ClaudeTransport] = None,
    examples: Optional[list[str]] = None,
    format_: Optional[str] = None,
    context: str = "",
    profile: Optional[ClientProfile] = None,
) -> tuple[dict[str, list[MicrocopyCandidate]], dict[str, str]]:
    """One call per field for the format's field set. With a profile, each candidate is tagged
    with its deterministic lint hits so the menu can mark options the gates would reject."""
    transport = transport or ClaudeTransport()
    fallback = {f.value: n for f, n in _FALLBACK_CAPS.items()}
    candidates_by_field: dict[str, list[MicrocopyCandidate]] = {}
    errors_by_field: dict[str, str] = {}
    for field_name, count in config.microcopy_fields(format_, fallback).items():
        candidates, error = generate_field(
            field_name, angle, count=count, transport=transport, examples=examples, context=context
        )
        if profile is not None:
            for c in candidates:
                c.flags = microcopy_lint.flags_for(c.text, profile)
        candidates_by_field[field_name] = candidates
        if error:
            errors_by_field[field_name] = error
    return candidates_by_field, errors_by_field
