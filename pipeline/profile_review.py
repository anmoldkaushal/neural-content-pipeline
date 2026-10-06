"""Human review of a client's profile, section by section: Draft -> Reviewed -> Final.

A profile drafted from documents (client_setup.draft_profile) is a first pass, so each section
carries its own status in clients/<id>/profile_review.json, with who set it and when, plus a hash
of the section's content at that moment. A section edited outside the app after it was marked
drops one step (final -> reviewed, reviewed -> draft) and says so, rather than silently keeping a
status for content nobody looked at. A Final section can't be saved until it is reopened.

Jobs still run on a profile that isn't final; run_job records which sections aren't, so the
package says the piece was written against an unreviewed profile.

Brief defaults (brief_defaults.yaml) live here too: the audience and must-follow a brief starts
from, client-wide and per content type, and any custom content types the client uses."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

import yaml

DRAFT_HEADER = "# DRAFT — agent-generated, review before use"
STATUSES = ("draft", "reviewed", "final")
SECTIONS = {
    "style_guide": "Style guide",
    "tone_presets": "Tone presets",
    "banned_words": "Banned words",
    "framing_rules": "Framing rules",
    "brief_defaults": "Brief defaults",
}
_REVIEW_FILE = "profile_review.json"
_STEP_DOWN = {"final": "reviewed", "reviewed": "draft"}


# ------------------------------------------------------------------ reading sections


def _yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _style_guide_body(client_dir: Path) -> str:
    """The style guide without its heading line, so the DRAFT marker in it isn't content."""
    path = client_dir / "style_guide.md"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("# Style guide"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def section_content(client_dir: Path, section: str) -> Any:
    if section == "style_guide":
        return _style_guide_body(client_dir)
    if section == "tone_presets":
        return _yaml(client_dir / "tone_presets.yaml").get("presets") or []
    if section == "banned_words":
        client = _yaml(client_dir / "client.yaml")
        return {
            "do_not_say": _yaml(client_dir / "constraints.yaml").get("do_not_say") or [],
            "extra_banned_words": client.get("extra_banned_words") or [],
            "extra_banned_phrases": client.get("extra_banned_phrases") or [],
        }
    if section == "framing_rules":
        return _yaml(client_dir / "constraints.yaml").get("do_not_frame") or []
    if section == "brief_defaults":
        return load_brief_defaults(client_dir)
    raise ValueError(f"unknown profile section: {section}")


def _hash(content: Any) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ status


def _load(client_dir: Path) -> dict[str, Any]:
    path = client_dir / _REVIEW_FILE
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("sections", {})
    if "flags" not in data:
        data["flags"] = [{"text": t, "resolved_by": None, "resolved_at": None} for t in _flags_in_style_guide(client_dir)]
    return data


def _save(client_dir: Path, data: dict[str, Any]) -> None:
    (client_dir / _REVIEW_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def status(client_dir: Path) -> dict[str, dict[str, Any]]:
    """Effective status per section: {"status", "by", "at", "changed"}. `changed` is set when the
    content no longer matches what was reviewed, and the status has already been stepped down."""
    stored = _load(client_dir)["sections"]
    out: dict[str, dict[str, Any]] = {}
    for section in SECTIONS:
        entry = stored.get(section) or {}
        st = entry.get("status", "draft")
        changed = st != "draft" and entry.get("hash") != _hash(section_content(client_dir, section))
        out[section] = {
            "status": _STEP_DOWN[st] if changed else st,
            "by": entry.get("by"),
            "at": entry.get("at"),
            "changed": f"changed since it was marked {st}" if changed else None,
        }
    return out


def not_final(client_dir: Path) -> list[str]:
    return [SECTIONS[s] for s, v in status(client_dir).items() if v["status"] != "final"]


def set_status(client_dir: Path, section: str, new_status: str, by: str) -> None:
    if section not in SECTIONS or new_status not in STATUSES:
        raise ValueError(f"bad section or status: {section} {new_status}")
    if not by.strip():
        raise ValueError("a reviewer name is required")
    data = _load(client_dir)
    data["sections"][section] = {
        "status": new_status,
        "by": by.strip(),
        "at": dt.datetime.now().isoformat(timespec="minutes"),
        "hash": _hash(section_content(client_dir, section)),
    }
    _save(client_dir, data)
    _sync_draft_headers(client_dir)


def _sync_draft_headers(client_dir: Path) -> None:
    """Drops a file's DRAFT marker once every section stored in it is final (and puts it back if
    one is reopened), so the marker in the file always agrees with the review status."""
    current = status(client_dir)
    final = {s for s, v in current.items() if v["status"] == "final"}
    for name, sections in (("tone_presets.yaml", {"tone_presets"}),
                           ("constraints.yaml", {"banned_words", "framing_rules"})):
        path = client_dir / name
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        has = bool(lines) and lines[0].startswith("# DRAFT")
        if sections <= final and has:
            path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
        elif not sections <= final and not has:
            path.write_text("\n".join([DRAFT_HEADER] + lines) + "\n", encoding="utf-8")
    guide = client_dir / "style_guide.md"
    if guide.exists():
        heading = "# Style guide" if "style_guide" in final else "# Style guide (DRAFT — agent-generated, review before use)"
        body = _style_guide_body(client_dir)
        guide.write_text(f"{heading}\n\n{body}\n", encoding="utf-8")
    # the edits above don't touch any section's content, so stored hashes still match


# ------------------------------------------------------------------ flags


def _flags_in_style_guide(client_dir: Path) -> list[str]:
    """Open questions the profile draft left for a person, written into the style guide as
    sentences that ask a reviewer to resolve something."""
    text = _style_guide_body(client_dir)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if re.search(r"review(er)? should|should be resolved|conflicts? with", s, re.I)]


def flags(client_dir: Path) -> list[dict[str, Any]]:
    return _load(client_dir)["flags"]


def add_flags(client_dir: Path, texts: list[str]) -> None:
    data = _load(client_dir)
    known = {f["text"] for f in data["flags"]}
    data["flags"] += [{"text": t, "resolved_by": None, "resolved_at": None} for t in texts if t and t not in known]
    _save(client_dir, data)


def resolve_flag(client_dir: Path, index: int, by: str, resolved: bool = True) -> None:
    data = _load(client_dir)
    flag = data["flags"][index]
    flag["resolved_by"] = by.strip() if resolved else None
    flag["resolved_at"] = dt.datetime.now().isoformat(timespec="minutes") if resolved else None
    _save(client_dir, data)


# ------------------------------------------------------------------ editing


def _guard(client_dir: Path, section: str) -> None:
    if status(client_dir)[section]["status"] == "final":
        raise ValueError(f"{SECTIONS[section]} is final; reopen it before editing")


def _write_yaml(path: Path, data: dict[str, Any], draft: bool) -> None:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)
    path.write_text((DRAFT_HEADER + "\n" if draft else "") + body, encoding="utf-8")


def _refresh_hash(client_dir: Path, section: str) -> None:
    """An edit made in the app keeps the section's status: the person editing is reviewing it."""
    data = _load(client_dir)
    entry = data["sections"].get(section)
    if entry:
        entry["hash"] = _hash(section_content(client_dir, section))
        _save(client_dir, data)


def save_section(client_dir: Path, section: str, content: Any) -> None:
    _guard(client_dir, section)
    if section == "style_guide":
        draft = status(client_dir)["style_guide"]["status"] != "final"
        heading = "# Style guide (DRAFT — agent-generated, review before use)" if draft else "# Style guide"
        (client_dir / "style_guide.md").write_text(f"{heading}\n\n{str(content).strip()}\n", encoding="utf-8")
    elif section == "tone_presets":
        presets = [{"name": p["name"].strip(), "description": (p.get("description") or "").strip(),
                    "sample_line": (p.get("sample_line") or "").strip()} for p in content if (p.get("name") or "").strip()]
        _write_yaml(client_dir / "tone_presets.yaml", {"presets": presets}, draft=True)
    elif section in ("banned_words", "framing_rules"):
        constraints = _yaml(client_dir / "constraints.yaml")
        if section == "framing_rules":
            constraints["do_not_frame"] = _clean(content)
        else:
            constraints["do_not_say"] = _clean(content.get("do_not_say", []))
            client_path = client_dir / "client.yaml"
            client = _yaml(client_path)
            client["extra_banned_words"] = _clean(content.get("extra_banned_words", []))
            client["extra_banned_phrases"] = _clean(content.get("extra_banned_phrases", []))
            client_path.write_text(yaml.safe_dump(client, sort_keys=False, allow_unicode=True), encoding="utf-8")
        constraints.setdefault("do_not_say", [])
        constraints.setdefault("do_not_frame", [])
        _write_yaml(client_dir / "constraints.yaml", constraints, draft=True)
    elif section == "brief_defaults":
        save_brief_defaults(client_dir, content)
    else:
        raise ValueError(f"unknown profile section: {section}")
    _refresh_hash(client_dir, section)
    _sync_draft_headers(client_dir)


def _clean(items: list[Any]) -> list[str]:
    out: list[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


# ------------------------------------------------------------------ brief defaults


def load_brief_defaults(client_dir: Path) -> dict[str, Any]:
    data = _yaml(client_dir / "brief_defaults.yaml")
    types = data.get("content_types") or {}
    return {
        "audience": str(data.get("audience") or ""),
        "must_follow": str(data.get("must_follow") or ""),
        "content_types": {str(k): {f: v for f, v in (v or {}).items() if v not in (None, "")} for k, v in types.items()},
    }


def save_brief_defaults(client_dir: Path, defaults: dict[str, Any]) -> None:
    types = {}
    for name, spec in (defaults.get("content_types") or {}).items():
        spec = {k: v for k, v in (spec or {}).items() if v not in (None, "")}
        if name.strip() and spec:
            types[name.strip()] = spec
    data = {"audience": (defaults.get("audience") or "").strip(),
            "must_follow": (defaults.get("must_follow") or "").strip(),
            "content_types": types}
    path = client_dir / "brief_defaults.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")


def add_content_type(client_dir: Path, name: str, description: str,
                     word_range: Optional[tuple[int, int]] = None) -> str:
    """Remembers a custom content type for this client; returns its key (a slug of the name).
    Bypasses the Final lock on brief defaults on purpose: adding a type is using the profile, not
    editing what was reviewed, and the hash refresh keeps the status honest about it."""
    key = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not key:
        raise ValueError("a custom content type needs a name")
    defaults = load_brief_defaults(client_dir)
    spec = defaults["content_types"].setdefault(key, {})
    spec.update({"label": name.strip(), "description": description.strip()})
    if word_range:
        spec.pop("target_word_count", None)  # a pre-range entry's single target, superseded
        spec["word_range"] = [int(word_range[0]), int(word_range[1])]
    save_brief_defaults(client_dir, defaults)
    _refresh_hash(client_dir, "brief_defaults")
    return key
