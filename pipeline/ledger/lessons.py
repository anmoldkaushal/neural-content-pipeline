"""What the judges keep saying about a client's drafts, turned into rules a human can adopt.

Every judged-gate finding from every round is appended to output/lessons/<client>.jsonl (runtime
data, never committed). When the same kind of note recurs across jobs, suggest_rules() asks for
candidate additions to the client's style guide, framing rules or banned phrases; a human accepts
each one (apply_suggestion) or ignores it. Nothing is added to a client's rules automatically:
a judge's recurring complaint is evidence, not a decision."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import ClientProfile, GateStatus, RevisionRound

JUDGED_GATES = {"voice_critic", "client_constraints_critic", "claims_critic"}
MIN_JOBS = 2  # a note from a single job is that draft's problem, not a pattern yet
TARGETS = {"style_guide", "do_not_frame", "banned_phrase"}
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "rule_suggest.md"


def _path(output_root: Path, client_id: str) -> Path:
    return output_root / "lessons" / f"{client_id}.jsonl"


def record(output_root: Path, client_id: str, job_id: str, rounds: list[RevisionRound]) -> int:
    path = _path(output_root, client_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    lines = [
        json.dumps({"job_id": job_id, "revision": r.revision, "gate": g.gate_name, "note": item, "at": now})
        for r in rounds
        for g in r.gate_results
        if g.gate_name in JUDGED_GATES and g.status == GateStatus.FAILED
        for item in g.flagged_items
    ]
    if lines:
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return len(lines)


def load(output_root: Path, client_id: str) -> list[dict[str, Any]]:
    path = _path(output_root, client_id)
    if not path.exists():
        return []
    notes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            notes.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return notes


def suggest_rules(
    output_root: Path, profile: ClientProfile, transport: Optional[ClaudeTransport] = None
) -> tuple[list[dict[str, str]], Optional[str]]:
    """(suggestions, reason none were made). Each suggestion: target, rule, why, evidence."""
    notes = load(output_root, profile.client_id)
    jobs = {n["job_id"] for n in notes}
    if len(jobs) < MIN_JOBS:
        return [], f"judge notes from {len(jobs)} job(s); suggestions need at least {MIN_JOBS}"

    transport = transport or ClaudeTransport()
    notes_block = "\n".join(f"- ({n['gate']}, job {n['job_id']}) {n['note']}" for n in notes[-150:])
    prompt = (
        f"{_PROMPT_PATH.read_text(encoding='utf-8')}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n\n"
        f"Current style guide:\n{profile.style_guide.strip() or '(empty)'}\n\n"
        f"Current framing rules:\n" + ("\n".join(f"- {r}" for r in profile.do_not_frame) or "(none)") + "\n\n"
        f"Current extra banned phrases: {', '.join(profile.banned_phrases) or '(none)'}\n\n"
        f"Judge notes across {len(jobs)} jobs:\n{notes_block}"
    )
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, list):
        return [], f"suggestion call unavailable: {result.error or 'reply was not a JSON array'}"
    suggestions = []
    for s in parsed:
        if isinstance(s, dict) and s.get("target") in TARGETS and str(s.get("rule", "")).strip():
            suggestions.append({
                "target": s["target"],
                "rule": str(s["rule"]).strip(),
                "why": str(s.get("why", "")).strip(),
                "evidence": str(s.get("evidence", "")).strip(),
            })
    return suggestions, None


def _split_header(text: str) -> tuple[str, str]:
    """Leading comment lines (a DRAFT marker, hand-written notes) kept apart so a YAML rewrite
    doesn't drop them."""
    lines = text.splitlines(keepends=True)
    n = 0
    while n < len(lines) and lines[n].lstrip().startswith("#"):
        n += 1
    return "".join(lines[:n]), "".join(lines[n:])


def _update_yaml(path: Path, key: str, value: str) -> None:
    header, body = _split_header(path.read_text(encoding="utf-8")) if path.exists() else ("", "")
    data = yaml.safe_load(body) or {}
    items = list(data.get(key) or [])
    if value.lower() not in {str(v).lower() for v in items}:
        items.append(value)
    data[key] = items
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def apply_suggestion(client_dir: Path, target: str, rule: str) -> Path:
    """Adds one accepted rule to the client's own files: additive only, like every client rule."""
    rule = rule.strip()
    if target == "do_not_frame":
        path = client_dir / "constraints.yaml"
        _update_yaml(path, "do_not_frame", rule)
    elif target == "banned_phrase":
        path = client_dir / "client.yaml"
        _update_yaml(path, "extra_banned_phrases", rule)
    elif target == "style_guide":
        path = client_dir / "style_guide.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        heading = "## Learned from review"
        if heading not in text:
            text = text.rstrip() + f"\n\n{heading}\n"
        if re.search(rf"^- {re.escape(rule)}$", text, re.MULTILINE) is None:
            text = text.rstrip() + f"\n- {rule}\n"
        path.write_text(text, encoding="utf-8")
    else:
        raise ValueError(f"unknown rule target {target!r}; expected one of {sorted(TARGETS)}")
    return path
