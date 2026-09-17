"""Bundles the final job output: draft + microcopy options + provenance manifest + compliance
report. This bundle IS the deliverable, not just the draft text."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.schemas import Draft, GateResult, KBEntry, MicrocopyCandidate


def build_package(
    job_id: str,
    draft: Draft,
    microcopy: dict[str, list[MicrocopyCandidate]],
    gate_results: list[GateResult],
    kb_entries: list[KBEntry],
) -> dict[str, Any]:
    used_entries = {e.id: e for e in kb_entries}
    provenance_manifest = [
        {
            "claim_id": cid,
            "claim": used_entries[cid].claim if cid in used_entries else "(unknown claim id)",
            "source_doc": used_entries[cid].source_doc if cid in used_entries else None,
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
