"""A client's ideal customer profiles (clients/<id>/icps.yaml): who a piece is written for.

Drafted from documents at onboarding next to the tone presets (client_setup.draft_profile),
reviewed as their own profile section (profile_review), and picked per job on the brief form. A
picked ICP is rendered once into Brief.icp_profile, so every stage that already reads the audience
line also sees the roles, pains and objections behind it.

ICPs are targeting context, not facts: the same rule kb_compile applies to "audience" statements.
Each prompt that receives one carries GUARD, and the entailment gate still holds every claim in a
draft to the verified knowledge base, so a pain point can shape an angle but never becomes a claim."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from pipeline.schemas import Icp

FILE = "icps.yaml"
LIST_FIELDS = ("roles", "pains", "cares_about", "objections")
GUARD = ("Reader profile (targeting context, not facts): shape the piece to this reader, but never "
         "state a pain, goal or objection below as a claim about the client, the reader or the market.")


def load(client_dir: Path) -> list[Icp]:
    """The client's ICPs; empty when the file is missing (clients that predate ICPs). A name typed
    twice by hand keeps its first entry here, so the app still loads; saving refuses the repeat."""
    path = client_dir / FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    items, seen = [], set()
    for item in (data or {}).get("icps") or []:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        if name not in seen:
            seen.add(name)
            items.append(item)
    return [Icp(**i) for i in normalize(items)]


def find(icps: list[Icp], name: str) -> Icp:
    for icp in icps:
        if icp.name == name:
            return icp
    available = ", ".join(i.name for i in icps) or "(none defined)"
    raise ValueError(f"unknown ICP {name!r}; available ICPs: {available}")


def normalize(items: list[Any]) -> list[dict[str, Any]]:
    """Cleans ICP dicts from YAML, a model reply or the UI editor: trims text, splits list fields
    given as one-per-line text, drops empty fields and nameless rows, and refuses duplicate names."""
    out: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if any(o["name"] == name for o in out):
            raise ValueError(f"ICP name {name!r} is used twice")
        icp: dict[str, Any] = {"name": name, "summary": str(item.get("summary") or "").strip()}
        company = str(item.get("company") or "").strip()
        if company:
            icp["company"] = company
        for field in LIST_FIELDS:
            value = item.get(field) or []
            if isinstance(value, str):
                value = value.splitlines()
            cleaned = [str(v).strip() for v in value if str(v or "").strip()]
            if cleaned:
                icp[field] = cleaned
        tone = str(item.get("default_tone") or "").strip()
        if tone:
            icp["default_tone"] = tone
        out.append(icp)
    return out


def render(icp: Icp) -> str:
    """The ICP as prompt text, GUARD first."""
    lines = [GUARD, f"ICP: {icp.name} -- {icp.summary}"]
    if icp.roles:
        lines.append(f"Roles: {', '.join(icp.roles)}")
    if icp.company:
        lines.append(f"Company: {icp.company}")
    for label, values in (("Pains", icp.pains), ("Cares about", icp.cares_about), ("Objections", icp.objections)):
        if values:
            lines.append(f"{label}: " + "; ".join(values))
    return "\n".join(lines)


def audience_text(icp: Icp) -> str:
    """A short audience line for the brief form, editable before the job starts."""
    text = icp.summary
    if icp.roles:
        text += f" ({', '.join(icp.roles)})"
    if icp.company:
        text += f". {icp.company}"
    return text.strip()


def default_tone(icp: Icp, preset_names: list[str]) -> Optional[str]:
    """The ICP's default tone if it still names a preset (a renamed preset just falls away)."""
    return icp.default_tone if icp.default_tone in preset_names else None
