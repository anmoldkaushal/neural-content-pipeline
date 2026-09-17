"""One JSON record per job: stages run, gate results, retry counts, human touchpoints. This is
deliberately NOT a full charter/ledger system like longevity-science-daily's ops/ — that solved a
multi-week-autonomous-session state-loss problem this repo doesn't have. A job starts, runs, and
ends; this is its audit trail, nothing more."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.schemas import JobRecord


def new_record(job_id: str, client_id: str, brief_summary: str) -> JobRecord:
    return JobRecord(job_id=job_id, client_id=client_id, brief_summary=brief_summary)


def save(output_dir: Path, record: JobRecord) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "job_record.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


def load(output_dir: Path) -> JobRecord:
    path = output_dir / "job_record.json"
    return JobRecord(**json.loads(path.read_text(encoding="utf-8")))
