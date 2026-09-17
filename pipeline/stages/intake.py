"""Loads a brief and checks it against what's available before any expensive work runs. Returns
a list of missing-info questions rather than drafting around a hole."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

import yaml

from pipeline.schemas import Brief


def load_brief(path: Union[str, Path]) -> Brief:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Brief(**data)


def missing_info(brief: Brief, client_dir: Path) -> list[str]:
    """Cheap, deterministic completeness checks — not an LLM call. Anything genuinely ambiguous
    still needs a human; this just catches the mechanical gaps first."""
    questions: list[str] = []

    kb_index_path = client_dir / "knowledge_base" / "kb_index.json"
    verified_count = 0
    if kb_index_path.exists():
        try:
            raw = json.loads(kb_index_path.read_text(encoding="utf-8"))
            verified_count = sum(1 for e in raw if e.get("status") == "verified")
        except json.JSONDecodeError:
            verified_count = 0

    if verified_count == 0:
        questions.append(
            f"No verified knowledge-base facts for client {brief.client_id!r} — run "
            f"`kb-compile` and `kb-verify` before drafting (an empty or all-pending KB still "
            f"blocks here, not just a missing file)."
        )

    if not brief.goal.strip():
        questions.append("Brief is missing a goal — what should this content accomplish?")
    if not brief.audience.strip():
        questions.append("Brief is missing an audience — who is this written for?")
    if not brief.format.strip():
        questions.append("Brief is missing a format (e.g. blog_post, email, social_post).")

    return questions
