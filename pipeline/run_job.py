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

The micro-copy menu is optional: `choose_angle` records the angle and tone without one, and
`execute` then drafts with no picked copy. A finished job can get its copy afterwards:
`build_microcopy_menu_after` -> `attach_microcopy`, held to the same gates.

A job that escalates can be finished by hand (finish_by_hand): the human's text goes through the
deterministic gates and becomes the package."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import yaml

from pipeline import config, icps, profile_review
from pipeline.gates import (
    claims_critic,
    judged,
    client_constraints,
    client_constraints_critic,
    entailment,
    microcopy_lint,
    sequence_lint,
    style_lint,
    voice_critic,
)
from pipeline.kb import content_plan, context, rulebook
from pipeline.kb.approved_examples import load_examples
from pipeline.kb.provenance import ProvenanceError, ensure_verified
from pipeline.ledger import job_record, lessons, stats
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
    sender = data.get("sender") or {}

    # The folder name is the client id everywhere else (CLI --client, job records); YAML scalars
    # are coerced to text so an unquoted 001 or 2024 can't fail validation.
    return ClientProfile(
        client_id=client_dir.name,
        company_name=str(data.get("company_name") or ""),
        industry=str(data.get("industry") or ""),
        website=str(data["website"]) if data.get("website") else None,
        synthetic=bool(data.get("synthetic", False)),
        tone_presets=tone_data.get("presets") or [],
        icps=icps.load(client_dir),
        banned_words=data.get("extra_banned_words") or [],
        banned_phrases=data.get("extra_banned_phrases") or [],
        do_not_say=constraints_data.get("do_not_say") or [],
        do_not_frame=constraints_data.get("do_not_frame") or [],
        style_guide=style_guide,
        sender_name=str(sender["name"]) if sender.get("name") else None,
        sign_off=str(sender["sign_off"]) if sender.get("sign_off") else None,
        utm=str(data["utm"]) if data.get("utm") else None,
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


def _approved_item(client_dir: Path, item_id: str) -> PlanItem:
    item = content_plan.get(client_dir, item_id)
    if item is None or item.status not in {"approved", "drafted"}:
        state = "not in the content plan" if item is None else f"{item.status}, not approved"
        raise JobBlocked(f"content plan item {item_id!r} is {state}")
    return item


def _sequence_items(client_dir: Path, brief: Brief, strict: bool = True) -> list[PlanItem]:
    """The plan items a sequence is written from, one per email in order. Not strict: the ones that
    still exist (later phases read them for prompts; start already checked them)."""
    if strict:
        return [_approved_item(client_dir, i) for i in brief.plan_item_ids]
    return [x for x in (content_plan.get(client_dir, i) for i in brief.plan_item_ids) if x is not None]


def _email_copy(draft: Draft) -> dict[str, str]:
    return {f: v for f, v in (("subject_line", draft.subject_line), ("preheader", draft.preheader)) if v}


def _email_copy_gate(draft: Draft, profile: ClientProfile) -> GateResult:
    """A sequence email's own subject line and preheader: present, and clean of house and client
    terms. Unlike picked copy this feeds the revise loop, since the writer wrote it with the body."""
    result = microcopy_lint.run(_email_copy(draft), profile)
    missing = [f"{f}: missing" for f in ("subject_line", "preheader") if not getattr(draft, f)]
    if not missing:
        return result
    flagged = result.flagged_items + missing
    return GateResult(gate_name="microcopy_lint", status=GateStatus.FAILED,
                      detail=f"{len(flagged)} micro-copy issue(s)", flagged_items=flagged)


def _plan_item(client_dir: Path, brief: Brief) -> Optional[PlanItem]:
    if not brief.plan_item_id:
        return None
    return _approved_item(client_dir, brief.plan_item_id)


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
    tone: Optional[ToneChoice] = None,
) -> JobSession:
    """preflight_ack: the preflight issues a human has seen and chosen to proceed past. None runs
    preflight; a list skips it and records those issues on the job."""
    client_dir = clients_root / client_id
    if not client_dir.exists():
        raise FileNotFoundError(f"no such client: {client_id} (looked in {client_dir})")
    transport = transport or ClaudeTransport()
    profile = _load_client_profile(client_dir)
    if brief.plan_item_ids:  # a sequence from the plan has one email per item
        brief = brief.model_copy(update={"sequence_length": len(brief.plan_item_ids)})
    elif brief.sequence_length is None and (n := config.sequence_length(brief.format)):
        brief = brief.model_copy(update={"sequence_length": n})
    # A picked ICP is rendered once here, so the session carries it even if the profile changes.
    if brief.icp and not brief.icp_profile:
        brief = brief.model_copy(update={"icp_profile": icps.render(icps.find(profile.icps, brief.icp))})

    # Preflight first and without creating a job: a brief that can't pass costs one cheap call and
    # leaves no half-started job behind.
    preflight_notes: list[str] = []
    if preflight_ack is None:
        # A plan-driven sequence's emails are known now, so each is checked against the rules too.
        steps = [content_plan.brief_block(i).strip() for i in _sequence_items(client_dir, brief, strict=False)]
        issues, preflight_notes = preflight.check(brief, profile, transport=transport, steps=steps)
        if issues:
            raise PreflightIssues(issues)

    job_id = uuid.uuid4().hex[:10]
    record = job_record.new_record(job_id=job_id, client_id=client_id, brief_summary=brief_summary or brief.goal[:160])
    record.format = brief.format
    record.icp = brief.icp
    record.plan_item_id = brief.plan_item_id
    record.preflight_overridden = list(preflight_ack or [])
    record.human_touchpoints.extend(preflight_notes)
    output_dir = output_root / "jobs" / job_id
    # Jobs run on an unreviewed profile, but the package says so (see profile_review.py).
    if unreviewed := profile_review.not_final(client_dir):
        record.human_touchpoints.append(f"client profile not final: {', '.join(unreviewed)}")

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
    items: list[PlanItem] = []
    try:
        item = _plan_item(client_dir, brief)
        items = _sequence_items(client_dir, brief)
    except JobBlocked as exc:
        gaps.append(str(exc))
    if brief.plan_item_ids and not config.sequence_length(brief.format):
        gaps.append(f"several plan items were picked, but {brief.format} writes one piece; use email_sequence")
    if gaps:
        _block(output_dir, record, "awaiting_human", gaps)
        raise JobBlocked("brief/knowledge-base incomplete: " + "; ".join(gaps))

    # 3. angle menu -- on the plan item, with the client context most relevant to the brief
    examples = load_examples(client_dir, brief.format)
    angles = angle_menu.generate_angles(
        brief, _load_kb_entries(client_dir), transport=transport, examples=examples,
        plan_block=content_plan.sequence_block(items) or content_plan.brief_block(item),
        context_block=context.select(client_dir, _context_query(brief, item, *(i.title for i in items))),
    )
    record.stages_run.append("angle_menu")
    for a in angles:  # the plan fixes one step per email; an angle that lost count gets the plan's
        if items and len(a.structure) != len(items):
            a.structure = [f"{i.title}: {i.notes or ''}".strip() for i in items]
    if not angles:
        _block(output_dir, record, "awaiting_human", ["angle generation unavailable or returned no candidates"])
        raise JobBlocked("no angle candidates available")

    # tone is optional here (build_microcopy_menu sets it); storing it early lets a UI resume the job
    session = JobSession(job_id=job_id, client_id=client_id, brief=brief, angles=angles, tone=tone,
                         preflight_overridden=list(preflight_ack or []))
    _save_session(output_root, session)
    record.status = "awaiting_selection"
    job_record.save(output_dir, record)
    return session


def choose_angle(job_id: str, angle_index: int, tone: ToneChoice, output_root: Path) -> JobSession:
    """Records the chosen angle and tone. Enough on its own for `execute`: skipping the micro-copy
    menu means the draft is written with no picked copy (the micro-copy gate passes on nothing)."""
    session = load_session(output_root, job_id)
    output_dir = output_root / "jobs" / job_id
    record = job_record.load(output_dir)
    session.angle_index = min(max(angle_index, 0), len(session.angles) - 1)
    session.tone = tone
    record.angle = session.angles[session.angle_index]
    record.tone = tone
    record.stages_run.append("tone_select")
    _save_session(output_root, session)
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
    session = choose_angle(job_id, angle_index, tone, output_root)
    output_dir = output_root / "jobs" / job_id
    record = job_record.load(output_dir)
    client_dir = clients_root / session.client_id
    profile = _load_client_profile(client_dir)

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
    name = f"email-{draft.piece}-rev-{draft.revision}" if draft.piece else f"rev-{draft.revision}"
    (drafts_dir / f"{name}.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")


def _run_gates(
    draft: Draft,
    selected: dict[str, str],
    microcopy_result: GateResult,
    profile: ClientProfile,
    kb_entries: list[KBEntry],
    brief: Brief,
    tone: ToneChoice,
    transport: ClaudeTransport,
    facts: str = "",
    earlier_voice_notes: Optional[list[str]] = None,
    seq: Optional[dict] = None,
) -> list[GateResult]:
    """`seq`, for one email of a sequence: its earlier emails, anchor words and the judges' sequence
    context. The email's own subject line and preheader then stand in for picked micro-copy."""
    if seq is not None:
        microcopy_result, selected = _email_copy_gate(draft, profile), _email_copy(draft)
    as_read = _with_microcopy(draft, selected)
    results = [
        microcopy_result,
        style_lint.run(draft, profile, word_range=brief.word_range, sentence_max=brief.sentence_max),
        client_constraints.run(draft, profile),
        client_constraints_critic.run(as_read, profile, transport=transport, facts=facts,
                                      sequence_context=seq["framing_context"] if seq else ""),
        entailment.run(draft, kb_entries),
        claims_critic.run(as_read, kb_entries, profile, transport=transport),
        voice_critic.run(as_read, profile, transport=transport, tone=tone, must_follow=brief.notes,
                         reader_profile=brief.icp_profile, facts=facts, earlier_notes=earlier_voice_notes,
                         sequence_context=seq["voice_context"] if seq else ""),
    ]
    if seq is not None:
        results.append(sequence_lint.run(draft, seq["earlier"], seq["anchor"], seq["other_anchors"]))
    return results


class _PieceFailed(Exception):
    """A piece used up its retry budget; carries what the caller needs to escalate it."""

    def __init__(self, draft: Draft, failed: list[GateResult], exhausted: list[GateResult], attempts: dict[str, int]):
        super().__init__(exhausted[0].gate_name)
        self.draft, self.failed, self.exhausted, self.attempts = draft, failed, exhausted, attempts


def _draft_piece(
    job_id: str,
    spec: dict,
    piece: Optional[int],
    record,
    output_dir: Path,
    gate_args: dict,
    draft_args: dict,
    retry_budget: int,
    transport: ClaudeTransport,
    seq: Optional[dict] = None,
) -> tuple[Draft, list[GateResult], dict[str, int]]:
    """One piece through draft -> gates -> bounded revise rounds; a revision edits the previous
    draft. Returns (draft, final gate results, attempts per gate); raises _PieceFailed when a gate
    is still failing after the retry budget."""
    current = draft_stage.write_draft(job_id, spec, transport=transport, revision=0, **draft_args)
    current = current.model_copy(update={"piece": piece})
    attempts: dict[str, int] = {}
    history: list[list[GateResult]] = []
    while True:
        label = f"email {piece} " if piece else ""
        record.stages_run.append(f"draft {label}(revision {current.revision})")
        _save_draft(output_dir, current)
        # The voice judge sees what it said in earlier rounds, so it can't ask for the opposite.
        earlier_voice = [i for round_ in history for g in round_ if g.gate_name == "voice_critic"
                         for i in (g.flagged_items or [g.detail])]
        gate_results = _run_gates(current, transport=transport, earlier_voice_notes=earlier_voice, seq=seq, **gate_args)
        record.rounds.append(RevisionRound(
            revision=current.revision, word_count=current.word_count,
            gate_results=gate_results, change_notes=current.change_notes, piece=piece,
        ))
        job_record.save(output_dir, record)

        failed = [g for g in gate_results if g.status == GateStatus.FAILED]
        if not failed:
            return current, gate_results, attempts
        for g in failed:
            attempts[g.gate_name] = attempts.get(g.gate_name, 0) + 1
        exhausted = [g for g in failed if not revise.should_retry(g, attempts[g.gate_name], retry_budget)]
        if exhausted:
            raise _PieceFailed(current, failed, exhausted, attempts)

        feedback = revise.next_revision_instruction(failed, history, banned=judged.banned_terms(gate_args["profile"]))
        history.append(failed)
        revised = draft_stage.revise_draft(
            job_id, spec, current, feedback, transport=transport,
            rules=draft_args["rules"], facts=draft_args["facts"], context_block=draft_args["context_block"],
        )
        current = revised.model_copy(update={"piece": piece})


def _combined(job_id: str, drafts: list[Draft], n: int, signature: str = "") -> Draft:
    """A sequence as one deliverable: each email under its own heading, with its subject line,
    preheader and the client's sign-off."""
    body = "\n\n".join(package.email_text(d, n, signature) for d in drafts)
    claims = list(dict.fromkeys(c for d in drafts for c in d.claims_used))
    return Draft(job_id=job_id, body=body, word_count=sum(d.word_count for d in drafts),
                 outline=[o for d in drafts for o in d.outline], claims_used=claims,
                 revision=max((d.revision for d in drafts), default=0))


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

    # 5. micro-copy gate -- a body redraft can't fix a picked subject line, so block, don't retry.
    # A sequence's sign-off is fixed client copy, so it is held to the same rules once, here.
    n = brief.sequence_length or 0
    signature = package.sign_off(profile) if n else ""
    microcopy_result = microcopy_lint.run(selected, profile)
    fixed = microcopy_lint.run({"sign_off": signature}, profile) if signature else microcopy_result
    for result in (microcopy_result, fixed):
        if result.status == GateStatus.FAILED:
            record.gate_results = [result]
            _block(output_dir, record, "awaiting_human", [f"micro-copy failed the gate: {result.flagged_items}"])
            raise JobBlocked("micro-copy violates house or client rules: " + "; ".join(result.flagged_items))
    if "{{" in signature:
        record.human_touchpoints.append(
            f"the sign-off has placeholders ({signature!r}): fill in `sender` in client.yaml, or map the merge "
            "fields in your sending tool")

    # 6. what the writer reads: rules, verified facts, plan item(s), relevant context
    items = _sequence_items(client_dir, brief, strict=False)
    plan_block = content_plan.sequence_block(items) or content_plan.brief_block(item)
    context_block = context.select(
        client_dir,
        _context_query(brief, item, chosen_angle.headline, chosen_angle.pitch, " ".join(chosen_angle.structure)),
    )
    rules = rulebook.render(profile, chosen_tone, brief.word_range, sentence_max=brief.sentence_max, sequence=bool(n))
    facts = draft_stage.facts_block(kb_entries, chosen_angle.claims_used)

    # 7. brief synthesis
    working_spec = brief_synthesis.synthesize(
        brief, chosen_angle, chosen_tone, kb_entries, transport=transport,
        plan_block=plan_block, context_block=context_block,
    )
    working_spec["approved_examples"] = load_examples(client_dir, brief.format)
    if brief.notes:
        working_spec["must_follow"] = brief.notes + (" (every email)" if n else "")
    if brief.sequence_notes:
        working_spec["sequence_notes"] = brief.sequence_notes + " (the sequence as a whole, not every email)"
    if brief.sentence_max:
        working_spec["sentence_max"] = brief.sentence_max
    if brief.word_range:
        working_spec["word_range"] = brief.word_range.label()
    if brief.format_description:
        working_spec["content_type"] = f"{brief.format}: {brief.format_description}"
    if item is not None:
        working_spec["content_plan_item"] = plan_block.strip()
    known_conflicts = list(session.preflight_overridden)
    if n and not items:
        # Steps the angle menu invented weren't there for preflight: check them now. A conflict is
        # recorded and the writer told the client's rules win (the angle was already chosen).
        bare = brief.model_copy(update={"notes": None, "sequence_notes": None, "angle_hint": None})
        conflicts, skipped = preflight.rule_conflicts(bare, profile, transport, steps=chosen_angle.structure)
        known_conflicts += conflicts
        record.human_touchpoints += [f"a planned email conflicts with a client rule (client rules applied): {c}"
                                     for c in conflicts] + ([skipped] if skipped else [])
    if known_conflicts:
        working_spec["known_conflicts"] = known_conflicts
    if brief.icp_profile:
        working_spec["reader_profile"] = brief.icp_profile
    if selected:
        working_spec["microcopy_selected"] = selected  # so the body doesn't repeat or contradict it
    record.stages_run.append("brief_synthesis")
    voice_reference = "" if working_spec["approved_examples"] else context.voice_reference(client_dir)

    # 8. draft, then gates and bounded revise rounds -- once, or once per email of a sequence,
    # each email knowing the ones before it and gated on its own
    steps = chosen_angle.structure
    gate_args = dict(selected=selected, microcopy_result=microcopy_result, profile=profile, kb_entries=kb_entries,
                     brief=brief, tone=chosen_tone, facts=facts)
    draft_args = dict(rules=rules, facts=facts, context_block=context_block, voice_reference=voice_reference)
    done: list[Draft] = []
    final_gates: list[GateResult] = []
    retries: dict[str, int] = {}
    for piece in (range(1, n + 1) if n else [None]):
        spec = working_spec
        if piece:
            spec = dict(working_spec)
            step = steps[piece - 1] if len(steps) >= piece else f"step {piece} of the arc"
            this_item = items[piece - 1] if len(items) >= piece else None
            spec["sequence"] = (
                f"Write ONLY email {piece} of {n} in the sequence. This email's job: {step}. Don't repeat "
                "what the earlier emails already said; move the reader one step on. Open and close it in "
                "a way none of the earlier emails did."
            )
            if this_item is not None:
                spec["content_plan_item"] = content_plan.brief_block(this_item).strip()
            spec["emails_already_written"] = [
                f"Email {d.piece} (subject: {d.subject_line or '-'}): {d.body}" for d in done]
            earlier = [f"Email {d.piece}: {d.body}" for d in done]
            plan_text = content_plan.brief_block(this_item)
            seq = dict(
                earlier=list(done),
                anchor=this_item.anchor if this_item else None,
                other_anchors=[i.anchor for i in items if i.anchor and i is not this_item],
                voice_context=judged.sequence_block(piece, n, step, plan_text, brief.sequence_notes or "", earlier),
                framing_context=judged.sequence_block(piece, n, step, plan_text, "", earlier, judge_repeats=False),
            )
        else:
            seq = None
        try:
            draft, gates, attempts = _draft_piece(job_id, spec, piece, record, output_dir, gate_args, draft_args,
                                                  effective_retry_budget, transport, seq=seq)
        except _PieceFailed as f:
            where = f"email {piece} of {n}: " if piece else ""
            for g, k in f.attempts.items():
                retries[f"{g} (email {piece})" if piece else g] = k
            record.retry_count = retries
            notes = [f"{where}gate {g.gate_name!r} failed after {f.attempts[g.gate_name]} attempt(s): {g.detail}"
                     for g in f.failed]
            if done:
                notes.append(f"emails 1-{len(done)} passed every gate; see drafts/")
            # Persist the failing draft so a human has something to actually review -- a gate
            # failure without the text that failed it is an escalation nobody can act on. For a
            # sequence, that's every email so far, so finishing by hand covers them all.
            failing = _combined(job_id, done + [f.draft], n, signature) if piece else f.draft
            (output_dir / "last_failed_draft.json").write_text(failing.model_dump_json(indent=2), encoding="utf-8")
            lessons.record(output_root, session.client_id, job_id, record.rounds)
            _block(output_dir, record, "awaiting_human", notes)
            primary = f.exhausted[0]
            raise revise.RetryBudgetExceeded(primary.gate_name, f.attempts[primary.gate_name], primary,
                                             also_failing=[g.gate_name for g in f.failed if g is not primary])
        done.append(draft)
        for g, k in attempts.items():
            retries[f"{g} (email {piece})" if piece else g] = k
        record.retry_count = retries
        final_gates += [g.model_copy(update={"gate_name": f"{g.gate_name} (email {piece})"}) for g in gates] if piece else gates
    current_draft = _combined(job_id, done, n, signature) if n else done[0]
    gate_results = final_gates
    record.gate_results = gate_results

    # 9. package
    final_package = package.build_package(
        job_id, current_draft, session.microcopy_menu, gate_results, kb_entries, microcopy_selected=selected
    )
    if n:
        final_package["sequence_length"] = n
        final_package["sequence"] = [d.model_dump(mode="json") for d in done]
        final_package["sign_off"] = signature
        package.write_sequence_csv(output_dir, done, profile)
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
    for planned in ([item] if item is not None else []) + items:
        content_plan.mark_drafted(client_dir, planned.id, job_id)

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
        style_lint.run(draft, profile, word_range=None if session.brief.sequence_length else session.brief.word_range,
                       sentence_max=None if session.brief.sequence_length else session.brief.sentence_max),
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
def _finished(output_root: Path, job_id: str) -> tuple[JobSession, dict, Path]:
    output_dir = output_root / "jobs" / job_id
    package_path = output_dir / "package.json"
    if not package_path.exists() or job_record.load(output_dir).status not in stats.COMPLETE_STATUSES:
        raise JobBlocked("micro-copy can only be added to a completed job")
    session = load_session(output_root, job_id)
    if session.angle_index is None or session.tone is None:
        raise JobBlocked("this job has no recorded angle and tone")
    return session, json.loads(package_path.read_text(encoding="utf-8")), output_dir


def _sequence_email(package_data: dict, piece: int) -> Draft:
    emails = package_data.get("sequence") or []
    if not 1 <= piece <= len(emails):
        raise JobBlocked(f"this job has no email {piece}")
    return Draft(**emails[piece - 1])


def build_microcopy_menu_after(
    job_id: str,
    clients_root: Path,
    output_root: Path,
    transport: Optional[ClaudeTransport] = None,
    piece: Optional[int] = None,
) -> JobSession:
    """A micro-copy menu for a finished job, written to fit the packaged draft (which may have been
    hand-edited) rather than only the angle. Leaves the package alone until `attach_microcopy`.
    With `piece`, alternatives for one sequence email's subject line and preheader, fitted to that
    email and kept apart from the other emails' subject lines."""
    session, package_data, _ = _finished(output_root, job_id)
    client_dir = clients_root / session.client_id
    profile = _load_client_profile(client_dir)
    body = package_data["draft"]["body"]
    fields = None
    item = content_plan.get(client_dir, session.brief.plan_item_id) if session.brief.plan_item_id else None
    context_lines = microcopy.menu_context(session.brief, session.tone, content_plan.brief_block(item), profile)
    if piece:
        email = _sequence_email(package_data, piece)
        body, fields = email.body, config.email_copy_fields(session.brief.format)
        others = [e.get("subject_line") for e in package_data["sequence"] if e.get("piece") != piece and e.get("subject_line")]
        context_lines += (f"This is email {piece} of {package_data.get('sequence_length')} in a sequence. Its current subject "
                          f"line: {email.subject_line or '(none)'}. The other emails' subject lines (yours must differ): "
                          f"{'; '.join(others) or '(none)'}\n")
    session.microcopy_menu, session.microcopy_errors = microcopy.generate_all(
        session.angles[session.angle_index],
        transport=transport or ClaudeTransport(),
        examples=load_examples(client_dir, session.brief.format),
        format_=session.brief.format,
        context=context_lines + f"Finished draft (fit the copy to it):\n{body}\n",
        profile=profile,
        fields=fields,
    )
    _save_session(output_root, session)
    return session


def attach_microcopy(
    job_id: str,
    microcopy_selected: dict[str, str],
    clients_root: Path,
    output_root: Path,
    transport: Optional[ClaudeTransport] = None,
    piece: Optional[int] = None,
) -> list[GateResult]:
    """Gates copy picked after drafting, then adds it to the package. The body is unchanged, so only
    the gates that read copy run: the micro-copy lint, then the three judged gates on the draft with
    the copy on top. Any failure leaves the package as it was (nothing to retry: the copy is the
    human's pick) and the results come back for the caller to show. With `piece`, the copy replaces
    that sequence email's subject line and/or preheader."""
    session, package_data, output_dir = _finished(output_root, job_id)
    client_dir = clients_root / session.client_id
    selected = {k: v.strip() for k, v in microcopy_selected.items() if v and v.strip()}
    if piece:
        return _attach_email_copy(session, package_data, output_dir, client_dir, piece, selected, transport)
    record = job_record.load(output_dir)
    profile = _load_client_profile(client_dir)

    results = [microcopy_lint.run(selected, profile)]
    if results[0].status != GateStatus.FAILED:
        transport = transport or ClaudeTransport()
        as_read = _with_microcopy(Draft(**package_data["draft"]), selected)
        results += [
            client_constraints_critic.run(as_read, profile, transport=transport),
            claims_critic.run(as_read, _load_kb_entries(client_dir), profile, transport=transport),
            voice_critic.run(as_read, profile, transport=transport, tone=session.tone, must_follow=session.brief.notes,
                             reader_profile=session.brief.icp_profile),
        ]
    if any(g.status == GateStatus.FAILED for g in results):
        return results

    # Replace the earlier results of these gates; the body's other gate results still stand.
    rerun = {g.gate_name: g for g in results}
    gate_results = [rerun.pop(g.gate_name, g) for g in record.gate_results] + list(rerun.values())
    record.gate_results = gate_results
    record.microcopy_selected = selected
    record.stages_run.append("microcopy (after draft)")
    package_data["microcopy_selected"] = selected
    package_data["microcopy"] = {f: [c.model_dump(mode="json") for c in cands] for f, cands in session.microcopy_menu.items()}
    package_data["compliance_report"] = {
        "gates": [g.model_dump(mode="json") for g in gate_results],
        "all_passed": all(g.status != GateStatus.FAILED for g in gate_results),
        "any_skipped": any(g.status == GateStatus.SKIPPED for g in gate_results),
    }
    package.write_package(output_dir, package_data)
    job_record.save(output_dir, record)
    pdf_export.render_package_pdf(package_data, record, profile.company_name, output_dir / "package.pdf")
    return results


def _attach_email_copy(session: JobSession, package_data: dict, output_dir: Path, client_dir: Path, piece: int,
                       selected: dict[str, str], transport: Optional[ClaudeTransport]) -> list[GateResult]:
    """attach_microcopy for one email of a sequence: lint, a subject line of its own, then the judged
    gates on the email with its new copy on top. Nothing changes unless every one passes."""
    record = job_record.load(output_dir)
    profile = _load_client_profile(client_dir)
    emails = [Draft(**e) for e in package_data["sequence"]]
    email = _sequence_email(package_data, piece)
    updated = email.model_copy(update={f: v for f, v in selected.items() if f in ("subject_line", "preheader")})
    others = [e for e in emails if e.piece != piece]
    clash = [f"the subject line is the same as email {e.piece}'s ({e.subject_line!r})" for e in others
             if updated.subject_line and e.subject_line and sequence_lint.same_copy(updated.subject_line, e.subject_line)]
    results = [_email_copy_gate(updated, profile),
               GateResult(gate_name="sequence_lint", status=GateStatus.FAILED if clash else GateStatus.PASSED,
                          detail="subject line repeats another email's" if clash else "subject line is its own",
                          flagged_items=clash)]
    if not any(g.status == GateStatus.FAILED for g in results):
        transport = transport or ClaudeTransport()
        as_read = _with_microcopy(updated, _email_copy(updated))
        results += [
            client_constraints_critic.run(as_read, profile, transport=transport),
            claims_critic.run(as_read, _load_kb_entries(client_dir), profile, transport=transport),
            voice_critic.run(as_read, profile, transport=transport, tone=session.tone, must_follow=session.brief.notes,
                             reader_profile=session.brief.icp_profile),
        ]
    results = [g.model_copy(update={"gate_name": f"{g.gate_name} (email {piece})"}) for g in results]
    if any(g.status == GateStatus.FAILED for g in results):
        return results

    emails[piece - 1] = updated
    n = int(package_data.get("sequence_length") or len(emails))
    signature = package_data.get("sign_off") or ""
    rerun = {g.gate_name: g for g in results}
    gate_results = [rerun.pop(g.gate_name, g) for g in record.gate_results] + list(rerun.values())
    record.gate_results = gate_results
    record.stages_run.append(f"email {piece} copy (after draft)")
    package_data["sequence"] = [e.model_dump(mode="json") for e in emails]
    package_data["draft"] = _combined(session.job_id, emails, n, signature).model_dump(mode="json")
    package_data["compliance_report"] = {
        "gates": [g.model_dump(mode="json") for g in gate_results],
        "all_passed": all(g.status != GateStatus.FAILED for g in gate_results),
        "any_skipped": any(g.status == GateStatus.SKIPPED for g in gate_results),
    }
    package.write_package(output_dir, package_data)
    package.write_sequence_csv(output_dir, emails, profile)
    job_record.save(output_dir, record)
    pdf_export.render_package_pdf(package_data, record, profile.company_name, output_dir / "package.pdf")
    return results


def run(
    client_id: str,
    brief_path: Path,
    clients_root: Path,
    output_root: Path,
    tone_preset: Optional[str] = None,
    angle_index: int = 0,
    retry_budget: Optional[int] = None,
    accept_preflight: bool = False,
    icp: Optional[str] = None,
) -> Path:
    """Non-interactive path for the CLI: all three phases back to back, taking the given angle,
    tone and ICP (or the brief's own `icp:`) and the first unflagged option for each micro-copy
    field. Without --tone, a picked ICP's default tone wins over the client's first preset. With
    accept_preflight, brief problems found before drafting are recorded and the job proceeds
    (client rules win)."""
    brief = intake.load_brief(brief_path)
    if icp:
        brief = brief.model_copy(update={"icp": icp, "icp_profile": None})
    transport = ClaudeTransport()
    try:
        session = start(client_id, brief, clients_root, output_root, transport=transport, brief_summary=str(brief_path))
    except PreflightIssues as exc:
        if not accept_preflight:
            raise
        session = start(client_id, brief, clients_root, output_root, transport=transport,
                        brief_summary=str(brief_path), preflight_ack=exc.issues)

    profile = _load_client_profile(clients_root / client_id)
    names = [t.name for t in profile.tone_presets]
    icp_tone = icps.default_tone(icps.find(profile.icps, brief.icp), names) if brief.icp else None
    preset_name = tone_preset or icp_tone or (names[0] if names else None)
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
