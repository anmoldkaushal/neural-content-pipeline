"""Pre-fills the UI's brief modifiers from what the engine already knows about a client, so a
human types the prompt and edits modifiers instead of writing a brief from scratch. Pure file
reads, no model call. For each field, first match wins: the client profile's defaults for this
content type, the profile's client-wide defaults, the latest local brief (local_briefs/<client>/)
of the same type, then config's default word count."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from pipeline import config, profile_review


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


def defaults_for(briefs_root: Path, client_id: str, format_: str, client_dir: Optional[Path] = None) -> dict[str, Any]:
    briefs = local_briefs(briefs_root, client_id)
    same_format = next((b for b in briefs if b.get("format") == format_), {})
    any_brief = briefs[0] if briefs else {}
    profile = profile_review.load_brief_defaults(client_dir) if client_dir else {"content_types": {}}
    per_type = profile["content_types"].get(format_, {})
    return {
        "goal": same_format.get("goal", ""),
        "audience": per_type.get("audience") or profile.get("audience") or same_format.get("audience")
        or any_brief.get("audience", ""),
        "notes": per_type.get("must_follow") or profile.get("must_follow") or same_format.get("notes", ""),
        "target_word_count": per_type.get("target_word_count") or same_format.get("target_word_count")
        or config.default_word_count(format_),
        "description": per_type.get("description", ""),
    }


def known_formats(briefs_root: Path, client_id: str, client_dir: Optional[Path] = None) -> list[str]:
    """Configured formats first, then this client's custom content types, then any other format
    its local briefs have used."""
    formats = list(config.formats())
    if client_dir:
        formats += [k for k in profile_review.load_brief_defaults(client_dir)["content_types"] if k not in formats]
    for b in local_briefs(briefs_root, client_id):
        f = b.get("format")
        if f and f not in formats:
            formats.append(f)
    return formats
