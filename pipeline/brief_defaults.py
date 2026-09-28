"""Pre-fills the UI's brief modifiers from what the engine already knows about a client, so a
human types the prompt and edits modifiers instead of writing a brief from scratch. Pure file
reads, no model call: the latest local brief of the same format for this client wins, then the
format's default word count from config."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pipeline import config


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


def defaults_for(briefs_root: Path, client_id: str, format_: str) -> dict[str, Any]:
    briefs = local_briefs(briefs_root, client_id)
    same_format = next((b for b in briefs if b.get("format") == format_), {})
    any_brief = briefs[0] if briefs else {}
    return {
        "goal": same_format.get("goal", ""),
        "audience": same_format.get("audience") or any_brief.get("audience", ""),
        "notes": same_format.get("notes", ""),
        "target_word_count": same_format.get("target_word_count") or config.default_word_count(format_),
    }


def known_formats(briefs_root: Path, client_id: str) -> list[str]:
    """Configured formats first, then any extra format this client's briefs have used."""
    formats = list(config.formats())
    for b in local_briefs(briefs_root, client_id):
        f = b.get("format")
        if f and f not in formats:
            formats.append(f)
    return formats
