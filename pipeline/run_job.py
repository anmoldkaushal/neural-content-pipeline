"""Orchestrator: chains stages, stops at first hard gate failure (mirrors run_cohort.py's
fail-closed chaining in neural-pnotp-gtm). Owns the bounded revise loop by calling into
pipeline/stages/revise.py's pure decision helpers rather than looping there."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import yaml

from pipeline import config
from pipeline.gates import client_constraints, entailment, style_lint, voice_critic
from pipeline.kb.provenance import ProvenanceError, ensure_verified
from pipeline.ledger import job_record
from pipeline.llm.transport import ClaudeTransport
from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus, KBEntry
from pipeline.stages import angle_menu, brief_synthesis, intake, microcopy, package, revise, self_check, tone_select
from pipeline.stages import draft as draft_stage


class JobBlocked(Exception):
    """Raised when the job cannot proceed without a human — never silently degrades to a
    worse-quality output instead."""


def _load_client_profile(client_dir: Path) -> ClientProfile:
    client_yaml_path = client_dir / "client.yaml"
    data = yaml.safe_load(client_yaml_path.read_text(encoding="utf-8")) or {}

    tone_presets_path = client_dir / "tone_presets.yaml"
    tone_data = yaml.safe_load(tone_presets_path.read_text(encoding="utf-8")) if tone_presets_path.exists() else {}
    tone_data = tone_data or {}

    constraints_path = client_dir / "constraints.yaml"
    constraints_data = (
        yaml.safe_load(constraints_path.read_text(encoding="utf-8")) if constraints_path.exists() else {}
    )
    constraints_data = constraints_data or {}

    return ClientProfile(
        client_id=data.get("client_id", client_dir.name),
        company_name=data.get("company_name", ""),
        industry=data.get("industry", ""),
        website=data.get("website"),
        synthetic=bool(data.get("synthetic", False)),
        tone_presets=tone_data.get("presets") or [],
        banned_words=data.get("extra_banned_words") or [],
        banned_phrases=data.get("extra_banned_phrases") or [],
        do_not_say=constraints_data.get("do_not_say") or [],
    )


def _load_kb_entries(client_dir: Path) -> list[KBEntry]:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    if not index_path.exists():
        return []
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    return [KBEntry(**e) for e in raw]


def run(
    client_id: str,
    brief_path: Path,
    clients_root: Path,
    output_root: Path,
    tone_preset: Optional[str] = None,
    angle_index: int = 0,
    retry_budget: Optional[int] = None,
) -> Path:
    job_id = uuid.uuid4().hex[:10]
    client_dir = clients_root / client_id
    if not client_dir.exists():
        raise FileNotFoundError(f"no such client: {client_id} (looked in {client_dir})")

    effective_retry_budget = retry_budget if retry_budget is not None else config.retry_budget()

    record = job_record.new_record(job_id=job_id, client_id=client_id, brief_summary=str(brief_path))
    output_dir = output_root / "jobs" / job_id

    # 1. provenance gate -- hard-blocks before any drafting work starts
    try:
        ensure_verified(client_dir)
    except ProvenanceError as exc:
        record.status = "failed"
        record.human_touchpoints.append(f"provenance gate blocked job: {'; '.join(exc.problems)}")
        job_record.save(output_dir, record)
        raise

    # 2. intake
    brief = intake.load_brief(brief_path)
    gaps = intake.missing_info(brief, client_dir)
    record.stages_run.append("intake")
    if gaps:
        record.status = "awaiting_human"
        record.human_touchpoints.extend(gaps)
        job_record.save(output_dir, record)
        raise JobBlocked("brief/knowledge-base incomplete: " + "; ".join(gaps))

    transport = ClaudeTransport()
    profile = _load_client_profile(client_dir)
    kb_entries = _load_kb_entries(client_dir)

    # 3. angle menu
    angles = angle_menu.generate_angles(brief, kb_entries, transport=transport)
    record.stages_run.append("angle_menu")
    if not angles:
        record.status = "awaiting_human"
        record.human_touchpoints.append("angle generation unavailable or returned no candidates")
        job_record.save(output_dir, record)
        raise JobBlocked("no angle candidates available")
    chosen_angle = angles[min(angle_index, len(angles) - 1)]

    # 4. tone select
    preset_name = tone_preset or (profile.tone_presets[0].name if profile.tone_presets else None)
    if not preset_name:
        record.status = "awaiting_human"
        record.human_touchpoints.append("no tone preset available or specified")
        job_record.save(output_dir, record)
        raise JobBlocked("no tone preset available")
    chosen_tone = tone_select.select_tone(profile, preset_name)
    record.stages_run.append("tone_select")

    # 5. brief synthesis
    working_spec = brief_synthesis.synthesize(brief, chosen_angle, chosen_tone, kb_entries, transport=transport)
    record.stages_run.append("brief_synthesis")

    # 6. draft + bounded revise loop (independent gates below)
    gate_results: list[GateResult] = []
    current_draft: Optional[Draft] = None
    attempts: dict[str, int] = {}
    revision = 0

    while True:
        current_draft = draft_stage.write_draft(job_id, working_spec, transport=transport, revision=revision)
        record.stages_run.append(f"draft (revision {revision})")

        self_check.check(current_draft, working_spec.get("claims_to_use", []), transport=transport)

        gate_results = [
            style_lint.run(current_draft, profile),
            client_constraints.run(current_draft, profile),
            entailment.run(current_draft, kb_entries),
            voice_critic.run(current_draft, profile, transport=transport),
        ]
        record.gate_results = gate_results

        failed = [g for g in gate_results if g.status == GateStatus.FAILED]
        if not failed:
            break

        primary_failure = failed[0]
        attempts[primary_failure.gate_name] = attempts.get(primary_failure.gate_name, 0) + 1
        record.retry_count = dict(attempts)

        if not revise.should_retry(primary_failure, attempts[primary_failure.gate_name], effective_retry_budget):
            record.status = "awaiting_human"
            record.human_touchpoints.append(
                f"gate {primary_failure.gate_name!r} failed after "
                f"{attempts[primary_failure.gate_name]} attempt(s): {primary_failure.detail}"
            )
            job_record.save(output_dir, record)
            raise revise.RetryBudgetExceeded(
                primary_failure.gate_name, attempts[primary_failure.gate_name], primary_failure
            )

        working_spec = dict(working_spec)
        working_spec["revision_instruction"] = revise.next_revision_instruction(primary_failure)
        revision += 1

    # 7. micro-copy (parallel lane, only needs the chosen angle)
    microcopy_candidates = microcopy.generate_all(chosen_angle, transport=transport)
    record.stages_run.append("microcopy")

    # 8. package
    assert current_draft is not None
    final_package = package.build_package(job_id, current_draft, microcopy_candidates, gate_results, kb_entries)
    package.write_package(output_dir, final_package)
    record.stages_run.append("package")
    record.status = "complete"
    record.human_touchpoints.append("final approval pending (v1: always required, see PIPELINE_STATUS.md)")
    job_record.save(output_dir, record)

    return output_dir
