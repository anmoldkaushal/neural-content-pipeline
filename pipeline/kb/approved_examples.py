"""Approved-example memory: nothing was remembered between jobs before this -- every draft started
cold from the same one-line tone description. Once a human explicitly runs `pipeline.cli approve`
on a finished job, its draft body is kept here as a real, confirmed example of that client's voice
for that exact format, and future angle/draft/microcopy prompts for the same client+format get it
as a reference. Scoped per format (an approved cold email is not shown as a voice example for a
LinkedIn job) because voice can legitimately differ by channel."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _examples_dir(client_dir: Path, format_: str) -> Path:
    return client_dir / "approved_examples" / format_


def record_approval(
    client_dir: Path,
    job_id: str,
    format_: str,
    package: dict[str, Any],
    approved_by: str,
    approved_at: str,
) -> Path:
    examples_dir = _examples_dir(client_dir, format_)
    examples_dir.mkdir(parents=True, exist_ok=True)
    body = str(package.get("draft", {}).get("body", ""))
    record = {
        "job_id": job_id,
        "format": format_,
        "body": body,
        "approved_by": approved_by,
        "approved_at": approved_at,
    }
    path = examples_dir / f"{job_id}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def load_examples(client_dir: Path, format_: str, limit: int = 2) -> list[str]:
    """Most recently approved bodies for this exact client+format, newest first."""
    examples_dir = _examples_dir(client_dir, format_)
    if not examples_dir.exists():
        return []
    files = sorted(examples_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    bodies: list[str] = []
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        body = str(data.get("body", "")).strip()
        if body:
            bodies.append(body)
    return bodies


def format_example_block(examples: list[str]) -> str:
    """Shared prompt fragment for angle_menu.py, draft.py, and microcopy.py -- empty string if
    there's nothing approved yet, so a client's first job reads exactly as it did before this."""
    if not examples:
        return ""
    joined = "\n\n---\n\n".join(examples)
    return (
        "\n\nPreviously approved copy for this exact client and format -- match this voice more "
        f"than the abstract tone description:\n{joined}\n"
    )
