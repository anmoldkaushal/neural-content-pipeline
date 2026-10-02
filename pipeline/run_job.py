"""Orchestrator: chains stages, stops at first hard gate failure (mirrors run_cohort.py's
fail-closed chaining in neural-pnotp-gtm). Owns the bounded revise loop by calling into
pipeline/stages/revise.py's pure decision helpers rather than looping there.

Three phases so a human can choose between them (the UI calls each; `run` chains them for the
CLI). Between phases, state lives in output/jobs/<id>/session.json:
  start                -> provenance gate, intake, preflight, angle menu
  build_microcopy_menu -> tone + chosen angle -> per-format micro-copy options (before any gate)
  execute              -> micro-copy gate, synthesis, draft, gates, revise loop, package, PDF

What the writer reads: the brief, the content plan item (if the brief names one), the client's
rules (rulebook), verified facts with their text, and the client-context sections most relevant
to the piece. What it may state: verified facts only -- entailment checks the declared ids and
claims_critic reads the prose.

A job that escalates can be finished by hand (finish_by_hand): the human's text goes through the
deterministic gates and becomes the package."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import yaml

from pipeline import config
from pipeline.gates import (
    claims_critic,
    client_constraints,
    client_constraints_critic,
    entailment,
    microcopy_lint,
    style_lint,
    voice_critic,
)
from pipeline.kb import content_plan, context, rulebook
from pipeline.kb.approved_examples import load_examples
from pipeline.kb.provenance import ProvenanceError, ensure_verified
from pipeline.ledger import job_record, lessons
from pipeline.llm.transport import ClaudeTransport
from pipeline.output import pdf_export
from pipeline.schemas import (
    Brief,
    ClientProfile,
    Draft,
    GateResult,
    GateStatus,
    JobSession,
    KBEntry,
    PlanItem,
    RevisionRound,
    ToneChoice,
)
from pipeline.stages import angle_menu, brief_synthesis, intake, microcopy, package, preflight, revise, tone_select
from pipeline.stages import draft as draft_stage


class JobBlocked(Exception):
    """Raised when the job cannot proceed without a human — never silently degrades to a
    worse-quality output instead."""


class PreflightIssues(JobBlocked):
    """The brief can't pass as written. No job is created: fix the brief, or call start() again
    with preflight_ack=issues to proceed knowing the client's rules win."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("brief problems found before drafting: " + " | ".join(issues))


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

    style_guide_path = client_dir / "style_guide.md"
    style_guide = style_guide_path.read_text(encoding="utf-8") if style_guide_path.exists() else ""

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
        style_guide=style_guide,
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


def _plan_item(client_dir: Path, brief: Brief) -> Optional[PlanItem]:
    if not brief.plan_item_id:
        return None
    item = content_plan.get(client_dir, brief.plan_item_id)
    if item is None or item.status not in {"approved", "drafted"}:
        state = "not in the content plan" if item is None else f"{item.status}, not approved"
        raise JobBlocked(f"content plan item {brief.plan_item_id!r} is {state}")
    return item


def _context_query(brief: Brief, item: Optional[PlanItem], *extra: str) -> str:
    parts = [brief.goal, brief.audience, brief.angle_hint or "", *extra]
    if item is not None:
        parts += [item.title, item.primary_keyword or "", item.cluster or "", item.notes or ""]
    return " ".join(p for p in parts if p)


def start(
    client_id: str,
    brief: Brief,
    clients_root: Path,
    output_root: Path,
    transport: Optional[ClaudeTransport] = None,
    brief_summary: Optional[str] = None,
    preflight_ack: Optional[list[str]] = None,
) -> JobSession:
    """preflight_ack: the preflight issues a human has seen and chosen to proceed past. None runs
    preflight; a list skips it and records those issues on the job."""
    client_dir = clients_root / client_id
    if not client_dir.exists():
        raise FileNotFoundError(f"no such client: {client_id} (looked in {client_dir})")
    transport = transport or ClaudeTransport()
    profile = _load_client_profile(client_dir)

    # Preflight first and without creating a job: a brief that can't pass costs one cheap call and
    # leaves no half-started job behind.
    preflight_notes: list[str] = []
    if preflight_ack is None:
        issues, preflight_notes = preflight.check(brief, profile, transport=transport)
        if issues:
            raise PreflightIssues(issues)

    job_id = uuid.uuid4().hex[:10]
    record = job_record.new_record(job_id=job_id, client_id=client_id, brief_summary=brief_summary or brief.goal[:160])
    record.format = brief.format
    record.plan_item_id = brief.plan_item_id
    record.preflight_overridden = list(preflight_ack or [])
    record.human_touchpoints.extend(preflight_notes)
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
    item: Optional[PlanItem] = None
    try:
        item = _plan_item(client_dir, brief)
    except JobBlocked as exc:
        gaps.append(str(exc))
    if gaps:
        _block(output_dir, record, "awaiting_human", gaps)
        raise JobBlocked("brief/knowledge-base incomplete: " + "; ".join(gaps))

    # 3. angle menu -- on the plan item, with the client context most relevant to the brief
    examples = load_examples(client_dir, brief.format)
    angles = angle_menu.generate_angles(
        brief, _load_kb_entries(client_dir), transport=transport, examples=examples,
        plan_block=content_plan.brief_block(item),
        context_block=context.select(client_dir, _context_query(brief, item)),
    )
    record.stages_run.append("angle_menu")
    if not angles:
        _block(output_dir, record, "awaiting_human", ["angle generation unavailable or returned no candidates"])
        raise JobBlocked("no angle candidates available")

    session = JobSession(job_id=job_id, client_id=client_id, brief=brief, angles=angles,
                         preflight_overridden=list(preflight_ack or []))
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
    item = content_plan.get(client_dir, session.brief.plan_item_id) if session.brief.plan_item_id else None
    session.microcopy_menu, session.microcopy_errors = microcopy.generate_all(
        record.angle,
        transport=transport or ClaudeTransport(),
        examples=load_examples(client_dir, session.brief.format),
        format_=session.brief.format,
        context=microcopy.menu_context(session.brief, tone, content_plan.brief_block(item), profile),
        profile=profile,
    )
    record.stages_run.append("microcopy")
    _save_session(output_root, session)
    job_record.save(output_dir, record)
    return session


def _save_draft(output_dir: Path, draft: Draft) -> None:
    drafts_dir = output_dir / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / f"rev-{draft.revision}.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")


def _run_gates(
    draft: Draft,
    selected: dict[str, str],
    microcopy_result: GateResult,
    profile: ClientProfile,
    kb_entries: list[KBEntry],
    brief: Brief,
    tone: ToneChoice,
    transport: ClaudeTransport,
) -> list[GateResult]:
    as_read = _with_microcopy(draft, selected)
    return [
        microcopy_result,
        style_lint.run(draft, profile, word_range=brief.word_range),
        client_constraints.run(draft, profile),
        client_constraints_critic.run(as_read, profile, transport=transport),
        entailment.run(draft, kb_entries),
        claims_critic.run(as_read, kb_entries, profile, transport=transport),
        voice_critic.run(as_read, profile, transport=transport, tone=tone),
    ]


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
    item = content_plan.get(client_dir, brief.plan_item_id) if brief.plan_item_id else None

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

    # 6. what the writer reads: rules, verified facts, plan item, relevant context
    plan_block = content_plan.brief_block(item)
    context_block = context.select(
        client_dir,
        _context_query(brief, item, chosen_angle.headline, chosen_angle.pitch, " ".join(chosen_angle.structure)),
    )
    rules = rulebook.render(profile, chosen_tone, brief.word_range)
    facts = draft_stage.facts_block(kb_entries, chosen_angle.claims_used)

    # 7. brief synthesis
    working_spec = brief_synthesis.synthesize(
        brief, chosen_angle, chosen_tone, kb_entries, transport=transport,
        plan_block=plan_block, context_block=context_block,
    )
    working_spec["approved_examples"] = load_examples(client_dir, brief.format)
    if brief.notes:
        working_spec["must_follow"] = brief.notes
    if brief.word_range:
        working_spec["word_range"] = brief.word_range.label()
    if item is not None:
        working_spec["content_plan_item"] = plan_block.strip()
    if session.preflight_overridden:
        working_spec["known_conflicts"] = session.preflight_overridden
    if selected:
        working_spec["microcopy_selected"] = selected  # so the body doesn't repeat or contradict it
    record.stages_run.append("brief_synthesis")
    voice_reference = "" if working_spec["approved_examples"] else context.voice_reference(client_dir)

    # 8. draft, then gates and bounded revise rounds; a revision edits the previous draft
    current_draft = draft_stage.write_draft(
        job_id, working_spec, transport=transport, revision=0,
        rules=rules, facts=facts, context_block=context_block, voice_reference=voice_reference,
    )
    attempts: dict[str, int] = {}
    history: list[list[GateResult]] = []

    while True:
        record.stages_run.append(f"draft (revision {current_draft.revision})")
        _save_draft(output_dir, current_draft)
        gate_results = _run_gates(current_draft, selected, microcopy_result, profile, kb_entries, brief, chosen_tone, transport)
        record.gate_results = gate_results
        record.rounds.append(RevisionRound(
            revision=current_draft.revision, word_count=current_draft.word_count,
            gate_results=gate_results, change_notes=current_draft.change_notes,
        ))
        job_record.save(output_dir, record)

        failed = [g for g in gate_results if g.status == GateStatus.FAILED]
        if not failed:
            break

        for g in failed:
            attempts[g.gate_name] = attempts.get(g.gate_name, 0) + 1
        record.retry_count = dict(attempts)
        exhausted = [g for g in failed if not revise.should_retry(g, attempts[g.gate_name], effective_retry_budget)]

        if exhausted:
            primary = exhausted[0]
            others = [g.gate_name for g in failed if g is not primary]
            notes = [
                f"gate {g.gate_name!r} failed after {attempts[g.gate_name]} attempt(s): {g.detail}" for g in failed
            ]
            # Persist the failing draft so a human has something to actually review -- a gate
            # failure without the text that failed it is an escalation nobody can act on.
            (output_dir / "last_failed_draft.json").write_text(current_draft.model_dump_json(indent=2), encoding="utf-8")
            lessons.record(output_root, session.client_id, job_id, record.rounds)
            _block(output_dir, record, "awaiting_human", notes)
            raise revise.RetryBudgetExceeded(primary.gate_name, attempts[primary.gate_name], primary, also_failing=others)

        feedback = revise.next_revision_instruction(failed, history)
        history.append(failed)
        current_draft = draft_stage.revise_draft(
            job_id, working_spec, current_draft, feedback, transport=transport,
            rules=rules, facts=facts, context_block=context_block,
        )

    # 9. package
    final_package = package.build_package(
        job_id, current_draft, session.microcopy_menu, gate_results, kb_entries, microcopy_selected=selected
    )
    package.write_package(output_dir, final_package)
    record.stages_run.append("package")
    record.status = "complete"
    if chosen_tone.ad_hoc:
        record.human_touchpoints.append(f"tone {chosen_tone.preset_name!r} was a one-off, not an approved preset")
    for issue in session.preflight_overridden:
        record.human_touchpoints.append(f"proceeded past a brief problem (client rules applied): {issue}")
    record.human_touchpoints.append("final approval pending (v1: always required, see PIPELINE_STATUS.md)")
    job_record.save(output_dir, record)
    lessons.record(output_root, session.client_id, job_id, record.rounds)
    if item is not None:
        content_plan.mark_drafted(client_dir, item.id, job_id)

    # 10. render -- the PDF is the deliverable a human actually reads; package.json stays the
    # machine-readable source of truth it's rendered from.
    pdf_export.render_package_pdf(final_package, record, profile.company_name, output_dir / "package.pdf")

    return output_dir


def finish_by_hand(
    job_id: str,
    body: str,
    clients_root: Path,
    output_root: Path,
    edited_by: str,
) -> tuple[bool, list[GateResult]]:
    """Packages a human's own edit of an escalated draft. The deterministic gates (house style and
    length, do-not-say) still apply -- a hand edit can't ship a banned word, and anything saved as
    an approved example later must be clean. The judged gates are not re-run: a human reviewer IS
    the judgement they stand in for. Returns (packaged, gate results)."""
    session = load_session(output_root, job_id)
    output_dir = output_root / "jobs" / job_id
    record = job_record.load(output_dir)
    if record.status == "complete":
        raise JobBlocked(f"job {job_id!r} is already complete")
    client_dir = clients_root / session.client_id
    profile = _load_client_profile(client_dir)
    kb_entries = _load_kb_entries(client_dir)

    last = output_dir / "last_failed_draft.json"
    previous = Draft(**json.loads(last.read_text(encoding="utf-8"))) if last.exists() else None
    draft = Draft(
        job_id=job_id, body=body.strip(), word_count=style_lint.count_words(body),
        outline=previous.outline if previous else [], claims_used=previous.claims_used if previous else [],
        revision=(previous.revision + 1) if previous else 0, change_notes=[f"edited by hand by {edited_by}"],
    )
    results = [
        microcopy_lint.run(record.microcopy_selected, profile),
        style_lint.run(draft, profile, word_range=session.brief.word_range),
        client_constraints.run(draft, profile),
    ] + [
        GateResult(gate_name=name, status=GateStatus.SKIPPED, detail=f"not re-run: finished by hand by {edited_by}")
        for name in ("client_constraints_critic", "claims_critic", "voice_critic")
    ]
    if any(r.status == GateStatus.FAILED for r in results):
        return False, results

    _save_draft(output_dir, draft)
    final_package = package.build_package(
        job_id, draft, session.microcopy_menu, results, kb_entries, microcopy_selected=record.microcopy_selected
    )
    final_package["human_edited"] = True
    package.write_package(output_dir, final_package)
    record.gate_results = results
    record.rounds.append(RevisionRound(revision=draft.revision, word_count=draft.word_count,
                                       gate_results=results, change_notes=draft.change_notes))
    record.human_edited = True
    record.status = "complete"
    record.stages_run.append("finished by hand")
    record.human_touchpoints.append(f"finished by hand by {edited_by}; judged gates not re-run")
    job_record.save(output_dir, record)
    if session.brief.plan_item_id:
        content_plan.mark_drafted(client_dir, session.brief.plan_item_id, job_id)
    pdf_export.render_package_pdf(final_package, record, profile.company_name, output_dir / "package.pdf")
    return True, results


def run(
    client_id: str,
    brief_path: Path,
    clients_root: Path,
    output_root: Path,
    tone_preset: Optional[str] = None,
    angle_index: int = 0,
    retry_budget: Optional[int] = None,
    accept_preflight: bool = False,
) -> Path:
    """Non-interactive path for the CLI: all three phases back to back, taking the given angle
    and tone and the first unflagged option for each micro-copy field. With accept_preflight,
    brief problems found before drafting are recorded and the job proceeds (client rules win)."""
    brief = intake.load_brief(brief_path)
    transport = ClaudeTransport()
    try:
        session = start(client_id, brief, clients_root, output_root, transport=transport, brief_summary=str(brief_path))
    except PreflightIssues as exc:
        if not accept_preflight:
            raise
        session = start(client_id, brief, clients_root, output_root, transport=transport,
                        brief_summary=str(brief_path), preflight_ack=exc.issues)

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
