"""Read-side view over output/jobs/*/job_record.json for the UI's job history: loads every record
(skipping any that no longer parse, and saying so) and summarizes how jobs went. "First pass"
means complete with no gate retries -- the draft cleared every gate the first time."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from pipeline.schemas import GateStatus, JobRecord


def load_records(output_root: Path, client_id: Optional[str] = None) -> tuple[list[JobRecord], list[str]]:
    records: list[JobRecord] = []
    unreadable: list[str] = []
    for path in sorted((output_root / "jobs").glob("*/job_record.json")):
        try:
            record = JobRecord(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - one malformed record shouldn't hide the rest
            unreadable.append(path.parent.name)
            continue
        if client_id is None or record.client_id == client_id:
            records.append(record)
    records.sort(key=lambda r: r.created_at, reverse=True)
    return records, unreadable


COMPLETE_STATUSES = ("complete", "complete_manual_edit")


def summarize(records: list[JobRecord]) -> dict[str, Any]:
    # Jobs still waiting on a menu choice haven't been attempted yet; they don't count toward rates.
    attempted = [r for r in records if r.status != "awaiting_selection"]
    # "complete_manual_edit" is a package a human finished by hand: delivered, so it counts.
    complete = [r for r in attempted if r.status in COMPLETE_STATUSES]
    first_pass = [r for r in complete if not r.retry_count]

    retries_by_gate: Counter[str] = Counter()
    failures_by_gate: Counter[str] = Counter()
    for r in attempted:
        retries_by_gate.update(r.retry_count)
        failures_by_gate.update(g.gate_name for g in r.gate_results if g.status == GateStatus.FAILED)

    def rate(n: int) -> Optional[float]:
        return n / len(attempted) if attempted else None

    return {
        "total": len(records),
        "attempted": len(attempted),
        "complete": len(complete),
        "awaiting_human": sum(1 for r in attempted if r.status == "awaiting_human"),
        "failed": sum(1 for r in attempted if r.status == "failed"),
        "awaiting_selection": len(records) - len(attempted),
        "completion_rate": rate(len(complete)),
        "first_pass_rate": rate(len(first_pass)),
        "retries_by_gate": dict(retries_by_gate),
        "failures_by_gate": dict(failures_by_gate),
    }
