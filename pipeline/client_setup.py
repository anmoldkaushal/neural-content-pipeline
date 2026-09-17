"""Scaffolds a new client from clients/_template/, optionally drafting a first-pass profile from
dropped documents (agent-drafts, human-approves-once — same pattern as kb_compile.py, applied to
onboarding itself)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from pipeline.ingest import ingest_document
from pipeline.llm.transport import ClaudeTransport, call_json

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "clients" / "_template"
_PROMPT_PATH = Path(__file__).resolve().parent / "llm" / "prompts" / "client_profile_draft.md"


def init_client(client_id: str, clients_root: Path, from_docs: Optional[Path] = None) -> Path:
    target = clients_root / client_id
    if target.exists():
        raise FileExistsError(f"clients/{client_id} already exists")

    shutil.copytree(_TEMPLATE_DIR, target)

    client_yaml = target / "client.yaml"
    text = client_yaml.read_text(encoding="utf-8")
    client_yaml.write_text(text.replace("client_id: TEMPLATE", f"client_id: {client_id}"), encoding="utf-8")

    if from_docs is not None:
        _draft_profile(target, from_docs)

    return target


def _draft_profile(client_dir: Path, from_docs: Path) -> None:
    transport = ClaudeTransport()
    doc_paths = [p for p in from_docs.iterdir() if p.is_file()] if from_docs.exists() else []
    combined_text = []
    for p in doc_paths:
        ingested = ingest_document(p)
        if ingested.raw_text.strip():
            combined_text.append(f"--- {p.name} ---\n{ingested.raw_text}")

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

    constraints_path = client_dir / "constraints.yaml"
    do_not_say = parsed.get("do_not_say", [])
    constraints_lines = ["# DRAFT — agent-generated, review before use"]
    if do_not_say:
        constraints_lines.append("do_not_say:")
        constraints_lines += [f"  - {json.dumps(term)}" for term in do_not_say]
    else:
        constraints_lines.append("do_not_say: []")
    constraints_path.write_text("\n".join(constraints_lines) + "\n", encoding="utf-8")
