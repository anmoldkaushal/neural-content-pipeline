"""Resolves a tone choice into the style checklist fed to brief_synthesis. Normally a
dropdown-level decision from the presets defined at onboarding; a human may instead type a one-off
tone for a single job (ad_hoc=True, recorded on the job and shown to voice_critic), or ask the
agent to suggest one. A suggested or ad-hoc tone becomes a preset only when a human saves it."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Brief, ClientProfile, TonePreset, ToneChoice

_SUGGEST_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "tone_suggest.md"


def select_tone(profile: ClientProfile, preset_name: str) -> ToneChoice:
    matches = [t for t in profile.tone_presets if t.name == preset_name]
    if not matches:
        available = ", ".join(t.name for t in profile.tone_presets) or "(none defined)"
        raise ValueError(f"unknown tone preset {preset_name!r}; available presets: {available}")

    preset = matches[0]
    return ToneChoice(
        preset_name=preset.name,
        resolved_style_checklist={
            "tone_description": preset.description,
            "sample_line": preset.sample_line,
        },
    )


def custom_tone(description: str, name: str = "Custom", sample_line: str = "") -> ToneChoice:
    description = description.strip()
    if not description:
        raise ValueError("a custom tone needs a description")
    return ToneChoice(
        preset_name=name.strip() or "Custom",
        resolved_style_checklist={"tone_description": description, "sample_line": sample_line},
        ad_hoc=True,
    )


def suggest_tone(
    profile: ClientProfile, brief: Brief, transport: Optional[ClaudeTransport] = None
) -> Optional[TonePreset]:
    """One short call proposing a tone the client's presets don't already cover. None if the
    transport is down or the reply is malformed -- the UI then just keeps the presets."""
    transport = transport or ClaudeTransport()
    existing = "\n".join(f"- {t.name}: {t.description}" for t in profile.tone_presets) or "(none)"
    prompt = (
        f"{_SUGGEST_PROMPT_PATH.read_text(encoding='utf-8')}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n"
        f"Content type: {brief.format}\nGoal: {brief.goal}\nAudience: {brief.audience}\n"
        + (f"{brief.icp_profile}\n" if brief.icp_profile else "")
        + "\n"
        f"Existing presets:\n{existing}"
    )
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, dict):
        return None
    try:
        return TonePreset(**parsed)
    except Exception:  # noqa: BLE001 - malformed suggestion is the same as no suggestion
        return None


def save_preset(client_dir: Path, preset: TonePreset) -> Path:
    """Appends a preset to tone_presets.yaml, keeping the file's leading comment lines (e.g. the
    '# DRAFT' review marker). Refuses a name that already exists rather than overwriting it."""
    path = client_dir / "tone_presets.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    header = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
    presets = (yaml.safe_load(text) or {}).get("presets") or []
    if any(p.get("name") == preset.name for p in presets):
        raise ValueError(f"tone preset {preset.name!r} already exists")
    presets.append(preset.model_dump())
    path.write_text(header + yaml.safe_dump({"presets": presets}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path
