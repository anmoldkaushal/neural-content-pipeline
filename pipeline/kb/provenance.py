"""Hard gate: a client's facts aren't usable until they're confirmed, sourced, and dated. Ported
directly from neural-pnotp-gtm/waterfall/verify_tenant.py's schema and refuse-loudly behavior —
a missing provenance.json is itself a problem, not treated as 'nothing to check'."""
from __future__ import annotations

import json
from pathlib import Path


class ProvenanceError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def verify_client(client_dir: Path) -> tuple[bool, list[str]]:
    problems: list[str] = []
    provenance_path = client_dir / "provenance.json"

    if not provenance_path.exists():
        return False, [f"no provenance.json found at {provenance_path}"]

    try:
        raw = json.loads(provenance_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, [f"provenance.json is not valid JSON: {exc}"]

    if not isinstance(raw, list):
        return False, ["provenance.json must be a JSON array of entries"]

    for i, entry in enumerate(raw):
        label = entry.get("claim", f"entry #{i}")
        source = entry.get("source") or entry.get("source_url")
        if not source:
            problems.append(f"{label!r}: missing source")
        if entry.get("confirmed") is not True:
            problems.append(f"{label!r}: not confirmed")
            continue
        if not entry.get("confirmed_by"):
            problems.append(f"{label!r}: confirmed but missing confirmed_by")
        if not entry.get("date"):
            problems.append(f"{label!r}: confirmed but missing date")

    return (len(problems) == 0), problems


def ensure_verified(client_dir: Path) -> None:
    ok, problems = verify_client(client_dir)
    if not ok:
        raise ProvenanceError(problems)
