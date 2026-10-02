"""Pre-fills the UI's brief modifiers from what the engine already knows about a client, so a
human types the prompt and edits modifiers instead of writing a brief from scratch. Pure file
reads, no model call: the latest local brief of the same format for this client wins, then the
format's default word range from config."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from pipeline import config
from pipeline.schemas import legacy_word_range


def local_briefs(briefs_root: Path, client_id: str) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for path in sorted((briefs_root / client_id).glob("*.y*ml"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            briefs.append(data)
    return briefs


def _brief_range(brief: dict[str, Any]) -> Optional[tuple[int, int]]:
    """A local brief's word range, reading a pre-range brief's single target as its range."""
    raw = brief.get("word_range")
    if isinstance(raw, dict) and raw.get("min") and raw.get("max"):
        return int(raw["min"]), int(raw["max"])
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return int(raw[0]), int(raw[1])
    if brief.get("target_word_count"):
        r = legacy_word_range(int(brief["target_word_count"]))
        return r.min, r.max
    return None


def defaults_for(briefs_root: Path, client_id: str, format_: str) -> dict[str, Any]:
    briefs = local_briefs(briefs_root, client_id)
    same_format = next((b for b in briefs if b.get("format") == format_), {})
    any_brief = briefs[0] if briefs else {}
    return {
        "goal": same_format.get("goal", ""),
        "audience": same_format.get("audience") or any_brief.get("audience", ""),
        "notes": same_format.get("notes", ""),
        "word_range": _brief_range(same_format) or config.default_word_range(format_),
    }


def known_formats(briefs_root: Path, client_id: str) -> list[str]:
    """Configured formats first, then any extra format this client's briefs have used."""
    formats = list(config.formats())
    for b in local_briefs(briefs_root, client_id):
        f = b.get("format")
        if f and f not in formats:
            formats.append(f)
    return formats
