"""Parallel lane, independent of the body draft: cheap, diverse candidates for short copy fields.
Runs concurrently with draft.py since neither depends on the other's output — both depend only on
the chosen angle. Caps are configurable per field type (config/pipeline_config.yaml)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline import config
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Angle, MicrocopyCandidate, MicrocopyField

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "microcopy.md"

_FALLBACK_CAPS: dict[MicrocopyField, int] = {
    MicrocopyField.TITLE: 3,
    MicrocopyField.SUBTITLE: 3,
    MicrocopyField.HOOK: 5,
    MicrocopyField.CTA: 5,
}


def generate_field(
    field: MicrocopyField,
    angle: Angle,
    count: Optional[int] = None,
    transport: Optional[ClaudeTransport] = None,
) -> list[MicrocopyCandidate]:
    transport = transport or ClaudeTransport()
    count = count or config.microcopy_cap(field.value, _FALLBACK_CAPS[field])

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    template = template.replace("{{FIELD_NAME}}", field.value).replace("{{COUNT}}", str(count))
    prompt = f"{template}\n\nAngle: {angle.headline} — {angle.pitch}"

    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, list):
        return []

    candidates: list[MicrocopyCandidate] = []
    for item in parsed[:count]:
        text = str(item.get("text", "")).strip()
        strategy = str(item.get("strategy", "")).strip()
        if text:
            candidates.append(MicrocopyCandidate(field=field, text=text, strategy=strategy))
    return candidates


def generate_all(
    angle: Angle, transport: Optional[ClaudeTransport] = None
) -> dict[str, list[MicrocopyCandidate]]:
    transport = transport or ClaudeTransport()
    return {f.value: generate_field(f, angle, transport=transport) for f in MicrocopyField}
