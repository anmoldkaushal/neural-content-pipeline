"""Bundles the final job output: draft + microcopy options + provenance manifest + compliance
report. This bundle IS the deliverable, not just the draft text."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional

from pipeline.schemas import ClientProfile, Draft, GateResult, KBEntry, MicrocopyCandidate


def build_package(
    job_id: str,
    draft: Draft,
    microcopy: dict[str, list[MicrocopyCandidate]],
    gate_results: list[GateResult],
    kb_entries: list[KBEntry],
    microcopy_selected: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    used_entries = {e.id: e for e in kb_entries}
    provenance_manifest = [
        {
            "claim_id": cid,
            "claim": used_entries[cid].claim if cid in used_entries else "(unknown claim id)",
            "source_doc": used_entries[cid].source_doc if cid in used_entries else None,
            # who confirmed the fact: a person's name, or the agent review (kb_triage.AGENT)
            "confirmed_by": used_entries[cid].confirmed_by if cid in used_entries else None,
        }
        for cid in draft.claims_used
    ]

    compliance_report = {
        "gates": [g.model_dump(mode="json") for g in gate_results],
        "all_passed": all(g.status.value != "failed" for g in gate_results),
        "any_skipped": any(g.status.value == "skipped" for g in gate_results),
    }

    return {
        "job_id": job_id,
        "draft": draft.model_dump(mode="json"),
        "microcopy_selected": microcopy_selected or {},
        "microcopy": {
            field: [c.model_dump(mode="json") for c in cands] for field, cands in microcopy.items()
        },
        "provenance_manifest": provenance_manifest,
        "compliance_report": compliance_report,
    }


def write_package(output_dir: Path, package: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "package.json"
    path.write_text(json.dumps(package, indent=2), encoding="utf-8")
    return path


def sign_off(profile: ClientProfile) -> str:
    """The client's sign-off and sender name, as appended under every email of a sequence."""
    return "\n".join(x for x in (profile.sign_off, profile.sender_name) if x)


def email_text(draft: Draft, n: int, signature: str = "") -> str:
    """One email of a sequence as a reader gets it: subject, preheader, body, sign-off."""
    parts = [f"EMAIL {draft.piece} OF {n}"]
    if draft.subject_line:
        parts.append(f"Subject: {draft.subject_line}")
    if draft.preheader:
        parts.append(f"Preheader: {draft.preheader}")
    parts.append(draft.body.strip())
    if signature:
        parts.append(signature)
    return "\n\n".join(parts)


def _tagged(text: str, profile: ClientProfile, n: int) -> str:
    """The website link with the client's UTM query for email n; unchanged without one."""
    if not (profile.utm and profile.website):
        return text
    joiner = "&" if "?" in profile.website else "?"
    return text.replace(profile.website, profile.website + joiner + profile.utm.replace("{n}", str(n)))


def write_sequence_csv(output_dir: Path, emails: list[Draft], profile: ClientProfile) -> Path:
    """sequence.csv: one row per email, ready to import into a sending tool. The UTM query goes on
    the link here only, so the gated body keeps the single plain link the client's rules name."""
    path = output_dir / "sequence.csv"
    signature = sign_off(profile)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["email", "subject_line", "preheader", "body"])
        for d in emails:
            body = d.body.strip() + (f"\n\n{signature}" if signature else "")
            writer.writerow([d.piece, d.subject_line or "", d.preheader or "", _tagged(body, profile, d.piece or 0)])
    return path
