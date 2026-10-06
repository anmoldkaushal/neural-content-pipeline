"""Scaffolds a new client from clients/_template/, optionally drafting a first-pass profile from
dropped documents (agent-drafts, human-approves-once — same pattern as kb_compile.py, applied to
onboarding itself)."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Optional

import yaml

from pipeline import icps, profile_review
from pipeline.ingest import ingest_document
from pipeline.llm.transport import ClaudeTransport, call_json

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "clients" / "_template"
_PROMPT_PATH = Path(__file__).resolve().parent / "llm" / "prompts" / "client_profile_draft.md"
_ICP_PROMPT_PATH = Path(__file__).resolve().parent / "llm" / "prompts" / "icp_draft.md"


_CLIENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def init_client(client_id: str, clients_root: Path, from_docs: Optional[Path] = None) -> Path:
    if not _CLIENT_ID.match(client_id):
        raise ValueError(f"client id {client_id!r} must be lowercase letters, digits, '-' or '_'")
    target = clients_root / client_id
    if target.exists():
        raise FileExistsError(f"clients/{client_id} already exists")

    shutil.copytree(_TEMPLATE_DIR, target)

    client_yaml = target / "client.yaml"
    text = client_yaml.read_text(encoding="utf-8")
    # Quoted: an id like 001 or yes would otherwise load back from YAML as a number or boolean.
    client_yaml.write_text(text.replace("client_id: TEMPLATE", f"client_id: {json.dumps(client_id)}"), encoding="utf-8")

    if from_docs is not None:
        draft_profile(target, from_docs)

    return target


def set_identity(client_dir: Path, company_name: str, industry: str, website: str = "") -> None:
    """Fills the identity fields of a scaffolded client.yaml, leaving everything else as is."""
    path = client_dir / "client.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.update(company_name=company_name, industry=industry, website=website)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _document_text(from_docs: Path) -> list[str]:
    doc_paths = [p for p in from_docs.iterdir() if p.is_file() and not p.name.startswith(".")] if from_docs.exists() else []
    combined_text = []
    for p in doc_paths:
        ingested = ingest_document(p)
        if ingested.raw_text.strip():
            combined_text.append(f"--- {p.name} ---\n{ingested.raw_text}")
    return combined_text


def _drafted_icps(raw: object, tone_names: set[str]) -> list[dict]:
    """A model's ICP list made safe to write: first of any repeated name wins, and a default tone
    that isn't one of the client's presets is dropped rather than left dangling."""
    items, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        if name and name not in seen:
            seen.add(name)
            items.append(item)
    cleaned = icps.normalize(items)
    for icp in cleaned:
        if icp.get("default_tone") not in tone_names:
            icp.pop("default_tone", None)
    return cleaned


def _write_icps(client_dir: Path, items: list[dict]) -> None:
    body = yaml.safe_dump({"icps": items}, sort_keys=False, allow_unicode=True, width=1000)
    (client_dir / icps.FILE).write_text(profile_review.DRAFT_HEADER + "\n" + body, encoding="utf-8")


def draft_profile(client_dir: Path, from_docs: Path) -> None:
    transport = ClaudeTransport()
    combined_text = _document_text(from_docs)

    if not combined_text:
        return  # nothing to draft from; leave the bare template in place

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = f"{template}\n\n" + "\n\n".join(combined_text)

    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, dict):
        note_path = client_dir / "style_guide.md"
        note_path.write_text(
            note_path.read_text(encoding="utf-8")
            + f"\n\n<!-- draft generation unavailable: {result.error} -->\n",
            encoding="utf-8",
        )
        return

    style_guide_path = client_dir / "style_guide.md"
    style_guide_path.write_text(
        "# Style guide (DRAFT — agent-generated, review before use)\n\n"
        f"{parsed.get('style_guide_summary', '')}\n",
        encoding="utf-8",
    )

    tone_path = client_dir / "tone_presets.yaml"
    presets = parsed.get("tone_presets", [])
    tone_yaml_lines = ["# DRAFT — agent-generated, review before use"]
    if presets:
        tone_yaml_lines.append("presets:")
        for preset in presets:
            tone_yaml_lines.append(f"  - name: {preset.get('name', 'unnamed')}")
            tone_yaml_lines.append(f"    description: {json.dumps(preset.get('description', ''))}")
            tone_yaml_lines.append(f"    sample_line: {json.dumps(preset.get('sample_line', ''))}")
    else:
        tone_yaml_lines.append("presets: []")
    tone_path.write_text("\n".join(tone_yaml_lines) + "\n", encoding="utf-8")

    tone_names = {str(p.get("name", "")) for p in presets if isinstance(p, dict)}
    _write_icps(client_dir, _drafted_icps(parsed.get("icps"), tone_names))

    constraints_path = client_dir / "constraints.yaml"
    do_not_say = parsed.get("do_not_say", [])
    do_not_frame = parsed.get("do_not_frame", [])
    constraints_lines = ["# DRAFT — agent-generated, review before use"]
    if do_not_say:
        constraints_lines.append("do_not_say:")
        constraints_lines += [f"  - {json.dumps(term)}" for term in do_not_say]
    else:
        constraints_lines.append("do_not_say: []")
    if do_not_frame:
        constraints_lines.append("do_not_frame:")
        constraints_lines += [f"  - {json.dumps(rule)}" for rule in do_not_frame]
    else:
        constraints_lines.append("do_not_frame: []")
    constraints_path.write_text("\n".join(constraints_lines) + "\n", encoding="utf-8")

    drafted = parsed.get("brief_defaults") or {}
    if isinstance(drafted, dict) and (drafted.get("audience") or drafted.get("must_follow")):
        defaults = profile_review.load_brief_defaults(client_dir)
        defaults["audience"] = defaults["audience"] or str(drafted.get("audience") or "")
        defaults["must_follow"] = defaults["must_follow"] or str(drafted.get("must_follow") or "")
        profile_review.save_brief_defaults(client_dir, defaults)
    profile_review.add_flags(client_dir, [str(f) for f in parsed.get("review_flags") or []])


def draft_icps(client_dir: Path, from_docs: Path, transport: Optional[ClaudeTransport] = None) -> int:
    """Drafts ICPs for a client that already exists (one onboarded before ICPs, or with new
    documents), from its documents plus the audience statements kb_compile set aside. Adds only
    names the client doesn't have yet, so a reviewed ICP is never overwritten; returns how many."""
    if profile_review.status(client_dir)["icps"]["status"] == "final":
        raise ValueError("ICPs are final; reopen them before drafting more")
    sources = _document_text(from_docs)
    index = client_dir / "knowledge_base" / "kb_index.json"
    if index.exists():
        notes = [e["claim"] for e in json.loads(index.read_text(encoding="utf-8")) if e.get("kind") == "audience"]
        if notes:
            sources.append("--- audience notes from the knowledge base ---\n" + "\n".join(f"- {n}" for n in notes))
    if not sources:
        raise ValueError("no documents or audience notes to draft ICPs from")

    existing = profile_review.section_content(client_dir, "icps")
    presets = profile_review.section_content(client_dir, "tone_presets")
    tone_names = {str(p.get("name", "")) for p in presets}
    prompt = (
        f"{_ICP_PROMPT_PATH.read_text(encoding='utf-8')}\n\n"
        f"Tone presets (default_tone must be one of these, or omitted): {', '.join(sorted(tone_names)) or '(none)'}\n"
        f"ICPs the client already has (do not repeat): {', '.join(i['name'] for i in existing) or '(none)'}\n\n"
        + "\n\n".join(sources)
    )
    parsed, result = call_json(transport or ClaudeTransport(), system_prompt="", user_prompt=prompt)
    if not result.ok:
        raise RuntimeError(f"ICP draft unavailable: {result.error}")
    raw = parsed.get("icps") if isinstance(parsed, dict) else parsed
    known = {i["name"] for i in existing}
    new = [i for i in _drafted_icps(raw, tone_names) if i["name"] not in known]
    if new:
        _write_icps(client_dir, existing + new)
    return len(new)
