"""Resolves a tone preset choice into the style checklist fed to brief_synthesis. A dropdown-level
decision, not a generation — presets live on the client profile, defined once at onboarding."""
from __future__ import annotations

from pipeline.schemas import ClientProfile, ToneChoice


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
