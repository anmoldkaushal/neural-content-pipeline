"""Orchestrator: chains stages, stops at first hard gate failure (mirrors run_cohort.py's
fail-closed chaining in neural-pnotp-gtm). Owns the bounded revise loop by calling into
pipeline/stages/revise.py's pure decision helpers rather than looping there.

Three phases so a human can choose between them (the UI calls each; `run` chains them for the
CLI). Between phases, state lives in output/jobs/<id>/session.json:
  start                -> provenance gate, intake, angle menu
  build_microcopy_menu -> tone + chosen angle -> per-format micro-copy options (before any gate)
  execute              -> micro-copy gate, synthesis, draft, gates, revise loop, package, PDF"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import yaml

from pipeline import config
from pipeline.gates import client_constraints, client_constraints_critic, entailment, microcopy_lint, style_lint, voice_critic
from pipeline.kb.approved_examples import load_examples
from pipeline.kb.provenance import ProvenanceError, ensure_verified
from pipeline.ledger import job_record
from pipeline.llm.transport import ClaudeTransport
from pipeline.output import pdf_export
from pipeline.schemas import Brief, ClientProfile, Draft, GateResult, GateStatus, JobSession, KBEntry, ToneChoice
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

    # The folder name is the client id everywhere else (CLI --client, job records); YAML scalars
    # are coerced to text so an unquoted 001 or 2024 can't fail validation.
    return ClientProfile(
        client_id=client_dir.name,
        company_name=str(data.get("company_name") or ""),
        industry=str(data.get("industry") or ""),
        website=str(data["website"]) if data.get("website") else None,
        synthetic=bool(data.get("synthetic", False)),
        tone_presets=tone_data.get("presets") or [],
        banned_words=data.get("extra_banned_words") or [],
        banned_phrases=data.get("extra_banned_phrases") or [],
        do_not_say=constraints_data.get("do_not_say") or [],
        do_not_frame=constraints_data.get("do_not_frame") or [],
    )


def _load_kb_entries(client_dir: Path) -> list[KBEntry]:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    if not index_path.exists():
        return []
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    return [KBEntry(**e) for e in raw]


def _session_path(output_root: Path, job_id: str) -> Path:
    return output_root / "jobs" / job_id / "session.json"


def load_session(output_root: Path, job_id: str) -> JobSession:
    return JobSession(**json.loads(_session_path(output_root, job_id).read_text(encoding="utf-8")))


def _save_session(output_root: Path, session: JobSession) -> None:
    path = _session_path(output_root, session.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def _block(output_dir: Path, record, status: str, notes: list[str]) -> None:
    record.status = status
    record.human_touchpoints.extend(notes)
    job_record.save(output_dir, record)


def _with_microcopy(draft: Draft, selected: dict[str, str]) -> Draft:
    """The draft as a reader meets it -- chosen micro-copy on top -- for the judged gates, so a
    subject line that frames the event wrongly is caught just like a body sentence would be."""
    if not selected:
        return draft
    lines = "\n".join(f"{field}: {text}" for field, text in selected.items())
    return draft.model_copy(update={"body": f"{lines}\n\n{draft.body}"})


def start(
    client_id: str,
    brief: Brief,
    clients_root: Path,
    output_root: Path,
    transport: Optional[ClaudeTransport] = None,
    brief_summary: Optional[str] = None,
) -> JobSession:
    job_id = uuid.uuid4().hex[:10]
    client_dir = clients_root / client_id
    if not client_dir.exists():
        raise FileNotFoundError(f"no such client: {client_id} (looked in {client_dir})")

    record = job_record.new_record(job_id=job_id, client_id=client_id, brief_summary=brief_summary or brief.goal[:160])
    record.format = brief.format
    output_dir = output_root / "jobs" / job_id

    # 1. provenance gate -- hard-blocks before any drafting work starts
    try:
        ensure_verified(client_dir)
    except ProvenanceError as exc:
        _block(output_dir, record, "failed", [f"provenance gate blocked job: {'; '.join(exc.problems)}"])
        raise

    # 2. intake
    gaps = intake.missing_info(brief, client_dir)
    record.stages_run.append("intake")
    if gaps:
        _block(output_dir, record, "awaiting_human", gaps)
        raise JobBlocked("brief/knowledge-base incomplete: " + "; ".join(gaps))

    # 3. angle menu
    transport = transport or ClaudeTransport()
    examples = load_examples(client_dir, brief.format)
    angles = angle_menu.generate_angles(brief, _load_kb_entries(client_dir), transport=transport, examples=examples)
    record.stages_run.append("angle_menu")
    if not angles:
        _block(output_dir, record, "awaiting_human", ["angle generation unavailable or returned no candidates"])
        raise JobBlocked("no angle candidates available")

    session = JobSession(job_id=job_id, client_id=client_id, brief=brief, angles=angles)
    _save_session(output_root, session)
    record.status = "awaiting_selection"
    job_record.save(output_dir, record)
    return session


def build_microcopy_menu(
    job_id: str,
    angle_index: int,
    tone: ToneChoice,
    clients_root: Path,
    output_root: Path,
    transport: Optional[ClaudeTransport] = None,
) -> JobSession:
    session = load_session(output_root, job_id)
    output_dir = output_root / "jobs" / job_id
    record = job_record.load(output_dir)
    client_dir = clients_root / session.client_id
    profile = _load_client_profile(client_dir)

    session.angle_index = min(max(angle_index, 0), len(session.angles) - 1)
    session.tone = tone
    record.angle = session.angles[session.angle_index]
    record.tone = tone
    record.stages_run.append("tone_select")

    # 4. micro-copy menu -- before any gate runs; each option carries its lint flags
    session.microcopy_menu, session.microcopy_errors = microcopy.generate_all(
        record.angle,
        transport=transport or ClaudeTransport(),
        examples=load_examples(client_dir, session.brief.format),
        format_=session.brief.format,
        context=microcopy.menu_context(session.brief, tone),
        profile=profile,
    )
    record.stages_run.append("microcopy")
    _save_session(output_root, session)
    job_record.save(output_dir, record)
    return session


def execute(
    job_id: str,
    microcopy_selected: dict[str, str],
    clients_root: Path,
    output_root: Path,
    transport: Optional[ClaudeTransport] = None,
    retry_budget: Optional[int] = None,
) -> Path:
    session = load_session(output_root, job_id)
    output_dir = output_root / "jobs" / job_id
    record = job_record.load(output_dir)
    if session.angle_index is None or session.tone is None:
        raise JobBlocked("pick an angle and tone (build_microcopy_menu) before executing")

    client_dir = clients_root / session.client_id
    profile = _load_client_profile(client_dir)
    kb_entries = _load_kb_entries(client_dir)
    brief = session.brief
    chosen_angle = session.angles[session.angle_index]
    chosen_tone = session.tone
    effective_retry_budget = retry_budget if retry_budget is not None else config.retry_budget()
    transport = transport or ClaudeTransport()

    record.status = "in_progress"
    selected = {k: v.strip() for k, v in microcopy_selected.items() if v and v.strip()}
    record.microcopy_selected = selected
    if session.microcopy_errors:
        fields = ", ".join(sorted(session.microcopy_errors))
        record.human_touchpoints.append(
            f"microcopy unavailable for: {fields} (call failed or returned nothing -- see per-field "
            f"errors: {session.microcopy_errors})"
        )

    # 5. micro-copy gate -- a body redraft can't fix a picked subject line, so block, don't retry
    microcopy_result = microcopy_lint.run(selected, profile)
    if microcopy_result.status == GateStatus.FAILED:
        record.gate_results = [microcopy_result]
        _block(output_dir, record, "awaiting_human", [f"selected micro-copy failed the gate: {microcopy_result.flagged_items}"])
        raise JobBlocked("selected micro-copy violates house or client rules: " + "; ".join(microcopy_result.flagged_items))

    # 6. brief synthesis
    working_spec = brief_synthesis.synthesize(brief, chosen_angle, chosen_tone, kb_entries, transport=transport)
    working_spec["approved_examples"] = load_examples(client_dir, brief.format)
    if brief.notes:
        working_spec["must_follow"] = brief.notes
    if selected:
        working_spec["microcopy_selected"] = selected  # so the body doesn't repeat or contradict it
    record.stages_run.append("brief_synthesis")

    # 7. draft + bounded revise loop (independent gates below)
    gate_results: list[GateResult] = []
    current_draft: Optional[Draft] = None
    attempts: dict[str, int] = {}
    revision = 0

    while True:
        current_draft = draft_stage.write_draft(job_id, working_spec, transport=transport, revision=revision)
        record.stages_run.append(f"draft (revision {revision})")

        self_check.check(current_draft, working_spec.get("claims_to_use", []), transport=transport)

        as_read = _with_microcopy(current_draft, selected)
        gate_results = [
            microcopy_result,
            style_lint.run(current_draft, profile),
            client_constraints.run(current_draft, profile),
            client_constraints_critic.run(as_read, profile, transport=transport),
            entailment.run(current_draft, kb_entries),
            voice_critic.run(as_read, profile, transport=transport, tone=chosen_tone),
        ]
        record.gate_results = gate_results

        failed = [g for g in gate_results if g.status == GateStatus.FAILED]
        if not failed:
            break

        primary_failure = failed[0]
        attempts[primary_failure.gate_name] = attempts.get(primary_failure.gate_name, 0) + 1
        record.retry_count = dict(attempts)

        if not revise.should_retry(primary_failure, attempts[primary_failure.gate_name], effective_retry_budget):
            _block(output_dir, record, "awaiting_human", [
                f"gate {primary_failure.gate_name!r} failed after "
                f"{attempts[primary_failure.gate_name]} attempt(s): {primary_failure.detail}"
            ])
            # Persist the failing draft so a human has something to actually review -- a gate
            # failure without the text that failed it is an escalation nobody can act on.
            (output_dir / "last_failed_draft.json").write_text(
                current_draft.model_dump_json(indent=2) if current_draft else "{}", encoding="utf-8"
            )
            raise revise.RetryBudgetExceeded(
                primary_failure.gate_name, attempts[primary_failure.gate_name], primary_failure
            )

        working_spec = dict(working_spec)
        working_spec["revision_instruction"] = revise.next_revision_instruction(primary_failure)
        revision += 1

    # 8. package
    assert current_draft is not None
    final_package = package.build_package(
        job_id, current_draft, session.microcopy_menu, gate_results, kb_entries, microcopy_selected=selected
    )
    package.write_package(output_dir, final_package)
    record.stages_run.append("package")
    record.status = "complete"
    if chosen_tone.ad_hoc:
        record.human_touchpoints.append(f"tone {chosen_tone.preset_name!r} was a one-off, not an approved preset")
    record.human_touchpoints.append("final approval pending (v1: always required, see PIPELINE_STATUS.md)")
    job_record.save(output_dir, record)

    # 9. render -- the PDF is the deliverable a human actually reads; package.json stays the
    # machine-readable source of truth it's rendered from.
    pdf_export.render_package_pdf(final_package, record, profile.company_name, output_dir / "package.pdf")

    return output_dir


def run(
    client_id: str,
    brief_path: Path,
    clients_root: Path,
    output_root: Path,
    tone_preset: Optional[str] = None,
    angle_index: int = 0,
    retry_budget: Optional[int] = None,
) -> Path:
    """Non-interactive path for the CLI: all three phases back to back, taking the given angle
    and tone and the first unflagged option for each micro-copy field."""
    brief = intake.load_brief(brief_path)
    transport = ClaudeTransport()
    session = start(client_id, brief, clients_root, output_root, transport=transport, brief_summary=str(brief_path))

    profile = _load_client_profile(clients_root / client_id)
    preset_name = tone_preset or (profile.tone_presets[0].name if profile.tone_presets else None)
    if not preset_name:
        output_dir = output_root / "jobs" / session.job_id
        _block(output_dir, job_record.load(output_dir), "awaiting_human", ["no tone preset available or specified"])
        raise JobBlocked("no tone preset available")
    tone = tone_select.select_tone(profile, preset_name)

    session = build_microcopy_menu(session.job_id, angle_index, tone, clients_root, output_root, transport=transport)
    selected = {
        field: next((c.text for c in cands if not c.flags), "")
        for field, cands in session.microcopy_menu.items()
    }
    return execute(session.job_id, selected, clients_root, output_root, transport=transport, retry_budget=retry_budget)
