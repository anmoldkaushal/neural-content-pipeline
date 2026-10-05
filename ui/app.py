"""Local UI over the pipeline: `streamlit run ui/app.py`.

Generate walks the three run_job phases with a human choice between each (angle -> micro-copy ->
draft + gates; the micro-copy step can be skipped, and copy added to a finished job instead); Knowledge base and Jobs are views over clients/ and output/jobs/.
Nothing here decides anything the pipeline doesn't -- it only collects choices and shows results."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline import brief_defaults, client_setup, run_job  # noqa: E402
from pipeline.kb.provenance import ProvenanceError, verify_client  # noqa: E402
from pipeline.ledger import job_record, stats  # noqa: E402
from pipeline.output import pdf_export  # noqa: E402
from pipeline.output.pdf_export import field_label  # noqa: E402
from pipeline.schemas import Brief, JobSession, TonePreset  # noqa: E402
from pipeline.stages import kb_add, kb_compile, kb_verify, revise, tone_select  # noqa: E402

CLIENTS_ROOT = REPO_ROOT / "clients"
OUTPUT_ROOT = REPO_ROOT / "output"
BRIEFS_ROOT = REPO_ROOT / "local_briefs"
CUSTOM_TONE = "Custom tone…"
WRITE_OWN = "Write my own…"
DOC_TYPES = ["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"]
MIN_WORDS, MAX_WORDS = 20, 4000

GATE_STATUS = {"passed": "✅ Passed", "failed": "❌ Failed", "skipped": "⚠️ Skipped"}
FACT_STATUS = {"pending": "Needs review", "verified": "Verified", "stale": "Stale",
               "rejected": "Deleted", "excluded": "Set aside"}
JOB_STATUS = {"complete": "✅ Complete", "complete_manual_edit": "✅ Complete (hand-edited)",
              "awaiting_human": "🙋 Needs a human", "awaiting_selection": "⏸ Waiting for a choice",
              "failed": "❌ Failed", "in_progress": "… In progress"}
STEPS = (("brief", "Brief"), ("angle", "Angle"), ("microcopy", "Micro-copy (optional)"), ("output", "Draft"))

st.set_page_config(page_title="Content Pipeline", layout="wide")


def _clients() -> list[str]:
    return sorted(p.name for p in CLIENTS_ROOT.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))


def _label(name: str) -> str:
    return field_label(name)


def _clamp_words(value) -> int:
    return min(max(int(value or 150), MIN_WORDS), MAX_WORDS)


def _save_uploads(files, client_dir: Path) -> None:
    docs_dir = client_dir / "knowledge_base" / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        (docs_dir / Path(f.name).name).write_bytes(f.getbuffer())


def _compile(client_dir: Path) -> str:
    with st.spinner("Extracting facts from documents (one model call per document)…"):
        entries, warnings = kb_compile.compile_kb(client_dir)
    pending = sum(1 for e in entries if e.status == "pending")
    return f"Compiled: {pending} fact(s) pending review." + (f" Warnings: {'; '.join(warnings)}" if warnings else "")


def _reset_job() -> None:
    for key in ("job_id", "step", "tone_choice", "blocked", "angle_pick"):
        st.session_state.pop(key, None)


def _seed(key: str, value) -> None:
    """Gives a keyed widget its starting value without also passing `value=` (Streamlit warns when
    both are set), so a value restored by _restore_brief wins over the pre-fill."""
    if key not in st.session_state:
        st.session_state[key] = value


def _restore_brief(session: JobSession) -> None:
    """Puts a job's brief and tone back into the brief form. Streamlit drops a widget's state once it
    stops rendering, so without this, going back from the angle step would wipe what was typed."""
    b, ss = session.brief, st.session_state
    k = f"{b.client_id}-{b.format}"
    ss[f"fmt-{b.client_id}"] = b.format
    ss[f"goal-{k}"], ss[f"aud-{k}"] = b.goal, b.audience
    ss[f"wc-{k}"] = _clamp_words(b.target_word_count)
    ss[f"hint-{k}"], ss[f"notes-{k}"] = b.angle_hint or "", b.notes or ""
    tone = session.tone
    if tone is None:
        return
    if tone.ad_hoc:
        ss[f"tone-{b.client_id}"] = CUSTOM_TONE
        ss[f"ctn-{b.client_id}"] = tone.preset_name
        ss[f"ctd-{b.client_id}"] = tone.resolved_style_checklist.get("tone_description", "")
        ss[f"cts-{b.client_id}"] = tone.resolved_style_checklist.get("sample_line", "")
    elif tone.preset_name in {t.name for t in profile.tone_presets}:
        ss[f"tone-{b.client_id}"] = tone.preset_name


def _stepper(current: str) -> None:
    names = [s for s, _ in STEPS]
    at = names.index(current)
    parts = []
    for i, (_, label) in enumerate(STEPS):
        text = f"{i + 1}. {label}"
        parts.append(f"**{text}**" if i == at else f":gray[{'✓ ' if i < at else ''}{text}]")
    st.markdown("  →  ".join(parts))


# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.title("Content Pipeline")
    if pending_client := st.session_state.pop("pending_client", None):
        st.session_state["client"] = pending_client  # set before the widget exists this run
    client_id = st.selectbox("Client", _clients(), key="client", on_change=_reset_job)
    client_dir = CLIENTS_ROOT / client_id
    profile = run_job._load_client_profile(client_dir)
    st.caption(f"{profile.company_name} · {profile.industry}")
    if profile.synthetic:
        st.info("Synthetic client (test data).")
    ok, problems = verify_client(client_dir)
    if ok:
        st.success("Provenance verified: ready to draft")
    else:
        st.error(f"Provenance blocks drafting ({len(problems)} issue(s))")
        with st.expander("Why"):
            for p in problems:
                st.caption(f"• {p}")
    reviewer = st.text_input("Your name", key="reviewer",
                             help="Recorded as confirmed_by when you verify, delete or add facts")

if flash := st.session_state.pop("flash", None):
    st.toast(flash)


def _gates(record) -> None:
    if not record.gate_results:
        return
    counts = {s: sum(1 for g in record.gate_results if g.status.value == s) for s in GATE_STATUS}
    summary = " · ".join(f"{n} {s}" for s, n in counts.items() if n)
    with st.container(border=True):
        st.markdown(f"**Gates** :gray[({summary})]")
        for g in record.gate_results:
            retries = record.retry_count.get(g.gate_name, 0)
            extra = f" :gray[· retried {retries}×]" if retries else ""
            st.markdown(f"{GATE_STATUS.get(g.status.value, g.status.value)} · `{g.gate_name}`{extra}  \n{g.detail}")
            for item in g.flagged_items:
                st.caption(f"• {item}")


def _microcopy_picker(session: JobSession, key: str) -> dict[str, str]:
    """One card per micro-copy field: the options (⚠ = the micro-copy gate would reject it) or your own."""
    selected: dict[str, str] = {}
    cols = st.columns(2)
    for n, (field, cands) in enumerate(session.microcopy_menu.items()):
        with cols[n % 2], st.container(border=True):
            st.markdown(f"**{field_label(field)}**")
            if field in session.microcopy_errors:
                st.caption(f"No options generated: {session.microcopy_errors[field]}")
            labels = [f"{'⚠ ' if c.flags else ''}{c.text}  :gray[_({c.strategy})_]" for c in cands] + [WRITE_OWN]
            default = next((i for i, c in enumerate(cands) if not c.flags), len(cands))
            pick = st.radio(field, range(len(labels)), index=default, format_func=lambda i, l=labels: l[i],
                            key=f"mc-{key}-{field}", label_visibility="collapsed")
            if pick == len(cands):
                selected[field] = st.text_input(f"Your {field_label(field).lower()}", key=f"own-{key}-{field}")
            else:
                selected[field] = cands[pick].text
                for flag in cands[pick].flags:
                    st.caption(f"⚠ {flag}")
    return selected


def _microcopy_after(job_id: str, where: str, has_copy: bool) -> None:
    """Micro-copy for a finished job: generate a menu that fits the draft, pick, then the picks are
    gated and added to the package (nothing changes if a gate fails)."""
    open_key = f"mcafter-{where}-{job_id}"
    run_n = st.session_state.get(open_key)
    if run_n is None:
        label = "Change micro-copy" if has_copy else "Add micro-copy"
        if st.button(label, key=f"{where}-mcbtn-{job_id}",
                     help="Generates options that fit this draft. Your picks are checked before they're added."):
            with st.spinner("Generating micro-copy options for this draft…"):
                try:
                    run_job.build_microcopy_menu_after(job_id, CLIENTS_ROOT, OUTPUT_ROOT)
                except run_job.JobBlocked as exc:
                    st.error(str(exc))
                    return
            st.session_state[open_key] = st.session_state.get(f"{open_key}-n", 0) + 1
            st.session_state[f"{open_key}-n"] = st.session_state[open_key]  # fresh widget keys per menu
            st.rerun()
        return

    session = run_job.load_session(OUTPUT_ROOT, job_id)
    with st.container(border=True):
        st.markdown("**Pick the micro-copy** :gray[· options marked ⚠ would be rejected by the micro-copy gate]")
        selected = _microcopy_picker(session, f"{where}-{job_id}-{run_n}")
        empty = [field_label(f).lower() for f, text in selected.items() if not text.strip()]
        with st.container(horizontal=True, vertical_alignment="center"):
            add = st.button("Check and add to package", type="primary", disabled=bool(empty),
                            key=f"{where}-mcadd-{job_id}")
            if st.button("Cancel", type="tertiary", key=f"{where}-mccancel-{job_id}"):
                st.session_state.pop(open_key, None)
                st.rerun()
            if empty:
                st.caption(f"To continue: write your {', '.join(empty)}.")
        if add:
            with st.spinner("Checking the copy against the gates…"):
                results = run_job.attach_microcopy(job_id, selected, CLIENTS_ROOT, OUTPUT_ROOT)
            failed = [g for g in results if g.status.value == "failed"]
            if failed:
                for g in failed:
                    st.error(f"Not added: {g.gate_name} failed: {g.detail}"
                             + "".join(f"\n- {item}" for item in g.flagged_items))
            else:
                st.session_state.pop(open_key, None)
                st.session_state["flash"] = "Micro-copy checked and added to the package and PDF."
                st.rerun()


def render_output(job_id: str, where: str) -> None:
    """A finished (or blocked) job: copyable text, gate results, downloads. `where` keeps widget
    keys unique when the same job shows on both the Generate and Jobs tabs."""
    output_dir = OUTPUT_ROOT / "jobs" / job_id
    record = job_record.load(output_dir)
    package_path = output_dir / "package.json"

    if not package_path.exists():
        failed_path = output_dir / "last_failed_draft.json"
        if failed_path.exists():
            body = json.loads(failed_path.read_text(encoding="utf-8")).get("body", "")
            st.warning("No package: this is the last draft, which failed a gate. See the gates below.")
            st.code(body, language=None, wrap_lines=True)
        elif record.status == "awaiting_selection":
            st.info("This job stopped before drafting.")
        _gates(record)
        if record.human_touchpoints:
            with st.expander("Notes for human review", expanded=True):
                for note in record.human_touchpoints:
                    st.markdown(f"- {note}")
        return

    package = json.loads(package_path.read_text(encoding="utf-8"))
    text_col, side_col = st.columns([3, 2], gap="large")
    with text_col:
        st.caption("Hover over a block and click its copy icon to copy it.")
        for field, text in (package.get("microcopy_selected") or {}).items():
            st.markdown(f"**{field_label(field)}**")
            st.code(text, language=None, wrap_lines=True)
        st.markdown("**Body**")
        st.code(package["draft"]["body"], language=None, wrap_lines=True)
        if record.status in stats.COMPLETE_STATUSES and (output_dir / "session.json").exists():
            _microcopy_after(job_id, where, has_copy=bool(package.get("microcopy_selected")))
        other = {f: c for f, c in (package.get("microcopy") or {}).items() if c}
        if other:
            with st.expander("Other micro-copy options"):
                for field, cands in other.items():
                    st.markdown(f"**{field_label(field)}**")
                    for c in cands:
                        st.markdown(f"- {c['text']}  _({c.get('strategy', '')})_")

    with side_col:
        pdf_path = output_dir / "package.pdf"
        with st.container(horizontal=True):
            if pdf_path.exists():
                st.download_button("Download PDF", pdf_path.read_bytes(), file_name=f"{record.client_id}-{job_id}.pdf",
                                   mime="application/pdf", key=f"{where}-pdf-{job_id}", type="primary")
            elif st.button("Make PDF", key=f"{where}-mkpdf-{job_id}",
                           help="This job has no PDF yet. Builds one from package.json (no model call)."):
                company = run_job._load_client_profile(CLIENTS_ROOT / record.client_id).company_name
                pdf_export.render_package_pdf(package, record, company, pdf_path)
                st.rerun()
            st.download_button("Download package.json", package_path.read_bytes(), file_name=f"{job_id}-package.json",
                               mime="application/json", key=f"{where}-json-{job_id}")
        _gates(record)
        if record.human_touchpoints:
            with st.expander("Notes for human review"):
                for note in record.human_touchpoints:
                    st.markdown(f"- {note}")


tab_generate, tab_kb, tab_jobs, tab_new = st.tabs(["Generate", "Knowledge base", "Jobs", "New client"])

# ---------------------------------------------------------------- generate

with tab_generate:
    step = st.session_state.get("step", "brief")
    _stepper(step)
    if step != "brief":
        job_id = st.session_state["job_id"]
        has_session = (OUTPUT_ROOT / "jobs" / job_id / "session.json").exists()  # absent on CLI-era jobs
        session = run_job.load_session(OUTPUT_ROOT, job_id) if has_session else None
        context = [f"Job `{job_id}`", f"tone: {st.session_state['tone_choice'].preset_name}"]
        if session is not None:
            context.insert(1, _label(session.brief.format))
            if step != "angle" and session.angle_index is not None:
                context.append(f"angle: {session.angles[session.angle_index].headline}")
        st.caption(" · ".join(context))

    if step == "brief":
        brief_col, tone_col = st.columns([3, 2], gap="large")
        with brief_col:
            formats = brief_defaults.known_formats(BRIEFS_ROOT, client_id)
            fmt = st.selectbox("Content type", formats, key=f"fmt-{client_id}", format_func=_label)
            d = brief_defaults.defaults_for(BRIEFS_ROOT, client_id, fmt)
            k = f"{client_id}-{fmt}"  # widget keys per client+format so switching re-prefills
            for key, value in ((f"goal-{k}", d["goal"]), (f"aud-{k}", d["audience"]), (f"notes-{k}", d["notes"]),
                               (f"wc-{k}", _clamp_words(d["target_word_count"])), (f"hint-{k}", "")):
                _seed(key, value)

            goal = st.text_area("Prompt", key=f"goal-{k}", height=110, placeholder="What should this piece do?")
            c1, c2 = st.columns(2)
            audience = c1.text_area("Audience", key=f"aud-{k}", height=100)
            notes = c2.text_area("Must follow", key=f"notes-{k}", height=100)
            c1, c2 = st.columns([1, 3])
            words = c1.number_input("Target words", MIN_WORDS, MAX_WORDS, step=10, key=f"wc-{k}")
            angle_hint = c2.text_input("Angle hint (optional)", key=f"hint-{k}")
            st.caption(
                "Audience, words and must-follow are pre-filled from this client's last brief of this type. "
                f"The gates enforce regardless: house style list, {len(profile.do_not_say)} do-not-say "
                f"term(s), {len(profile.do_not_frame)} framing rule(s), verified KB facts only."
            )

        with tone_col, st.container(border=True):
            st.markdown("**Tone**")
            options = [t.name for t in profile.tone_presets] + [CUSTOM_TONE]
            tone_pick = st.radio("Tone", options, key=f"tone-{client_id}", label_visibility="collapsed")
            if tone_pick != CUSTOM_TONE:
                preset = next(t for t in profile.tone_presets if t.name == tone_pick)
                st.caption(f"{preset.description}  \n_“{preset.sample_line}”_")
            else:
                if st.button("Suggest a tone", help="One short model call proposing a tone the presets don't cover"):
                    with st.spinner("Asking for a tone suggestion…"):
                        brief_for_tone = Brief(client_id=client_id, goal=goal, audience=audience, format=fmt)
                        suggestion = tone_select.suggest_tone(profile, brief_for_tone)
                    if suggestion:
                        st.session_state[f"ctn-{client_id}"] = suggestion.name
                        st.session_state[f"ctd-{client_id}"] = suggestion.description
                        st.session_state[f"cts-{client_id}"] = suggestion.sample_line
                    else:
                        st.error("No suggestion (model unavailable or reply malformed).")
                custom_name = st.text_input("Tone name", key=f"ctn-{client_id}")
                custom_desc = st.text_area("Describe the tone", key=f"ctd-{client_id}", height=80)
                custom_sample = st.text_input("Sample line (optional)", key=f"cts-{client_id}")
                save_it = st.checkbox("Save as a preset for this client", key=f"ctsave-{client_id}")

        missing = []
        if not ok:
            missing.append("clear the provenance issues in the sidebar")
        if not goal.strip():
            missing.append("write a prompt")
        if tone_pick == CUSTOM_TONE and not custom_desc.strip():
            missing.append("describe the custom tone")
        with st.container(horizontal=True, vertical_alignment="center"):
            go = st.button("Generate angles", type="primary", disabled=bool(missing))
            if missing:
                st.caption("To continue: " + "; ".join(missing) + ".")
        if go:
            try:
                if tone_pick == CUSTOM_TONE:
                    tone = tone_select.custom_tone(custom_desc, custom_name, custom_sample)
                else:
                    tone = tone_select.select_tone(profile, tone_pick)
                brief = Brief(client_id=client_id, goal=goal.strip(), audience=audience.strip(), format=fmt,
                              target_word_count=int(words), angle_hint=angle_hint.strip() or None,
                              notes=notes.strip() or None)
                with st.spinner("Checking provenance and generating angles…"):
                    session = run_job.start(client_id, brief, CLIENTS_ROOT, OUTPUT_ROOT, tone=tone)
            except (ValueError, ProvenanceError, run_job.JobBlocked) as exc:
                st.error(str(exc))
            else:
                if tone_pick == CUSTOM_TONE and save_it:  # only once the job has really started
                    try:
                        tone_select.save_preset(client_dir, TonePreset(
                            name=tone.preset_name, description=custom_desc.strip(), sample_line=custom_sample.strip()))
                        tone.ad_hoc = False
                        st.session_state["flash"] = f"Saved tone preset “{tone.preset_name}”."
                    except ValueError as exc:
                        st.session_state["flash"] = f"Tone not saved: {exc}"
                st.session_state.update(job_id=session.job_id, step="angle", tone_choice=tone)
                st.rerun()

    elif step == "angle":
        st.subheader("Choose an angle")
        per_row = min(len(session.angles), 3)
        cols = st.columns(per_row)
        chosen_angle = None
        for i, a in enumerate(session.angles):
            with cols[i % per_row], st.container(border=True):
                st.markdown(f"**{a.headline}**")
                st.write(a.pitch)
                if a.structure:
                    st.caption("\n".join(f"{n}. {s}" for n, s in enumerate(a.structure, 1)))
                picked = st.session_state.get("angle_pick") == i
                if st.button("✓ Selected" if picked else "Use this angle", key=f"angle-{job_id}-{i}",
                             type="primary" if picked else "secondary"):
                    chosen_angle = i
        if chosen_angle is not None:
            st.session_state["angle_pick"] = chosen_angle
            st.rerun()
        angle_pick = st.session_state.get("angle_pick")
        with st.container(horizontal=True, vertical_alignment="center"):
            if st.button("← Edit brief", type="tertiary"):
                _restore_brief(session)
                _reset_job()
                st.rerun()
            pick_copy = st.button("Choose micro-copy", type="primary", disabled=angle_pick is None,
                                  help="Subject lines, openers, calls to action and so on, picked before drafting")
            skip_copy = st.button("Skip micro-copy and write draft", disabled=angle_pick is None,
                                  help="Drafts the body only. You can add micro-copy to the finished piece.")
            if angle_pick is None:
                st.caption("To continue: pick an angle.")
        if pick_copy:
            with st.spinner("Generating micro-copy options…"):
                run_job.build_microcopy_menu(job_id, angle_pick, st.session_state["tone_choice"], CLIENTS_ROOT, OUTPUT_ROOT)
            st.session_state["step"] = "microcopy"
            st.rerun()
        if skip_copy:
            st.session_state.pop("blocked", None)
            run_job.choose_angle(job_id, angle_pick, st.session_state["tone_choice"], OUTPUT_ROOT)
            try:
                with st.spinner("Drafting and running gates. This is the slow step, usually a minute or more…"):
                    run_job.execute(job_id, {}, CLIENTS_ROOT, OUTPUT_ROOT)
            except (run_job.JobBlocked, revise.RetryBudgetExceeded) as exc:
                st.session_state["blocked"] = str(exc)
            st.session_state["step"] = "output"
            st.rerun()

    elif step == "microcopy":
        st.subheader("Pick the micro-copy")
        st.caption("Options marked ⚠ would be rejected by the micro-copy gate. Pick another or write your own.")
        selected = _microcopy_picker(session, job_id)
        empty = [field_label(f).lower() for f, text in selected.items() if not text.strip()]
        with st.container(horizontal=True, vertical_alignment="center"):
            if st.button("← Back to angles", type="tertiary"):
                st.session_state["step"] = "angle"
                st.session_state["angle_pick"] = session.angle_index
                st.rerun()
            write = st.button("Write draft and run gates", type="primary", disabled=bool(empty))
            if empty:
                st.caption(f"To continue: write your {', '.join(empty)}.")
        if write:
            st.session_state.pop("blocked", None)
            try:
                with st.spinner("Drafting and running gates. This is the slow step, usually a minute or more…"):
                    run_job.execute(job_id, selected, CLIENTS_ROOT, OUTPUT_ROOT)
            except (run_job.JobBlocked, revise.RetryBudgetExceeded) as exc:
                st.session_state["blocked"] = str(exc)
            st.session_state["step"] = "output"
            st.rerun()

    elif step == "output":
        if blocked := st.session_state.get("blocked"):  # kept until "New piece", so a rerun doesn't lose it
            st.error(f"Stopped for a human: {blocked}")
        render_output(job_id, "gen")
        with st.container(horizontal=True):
            if st.button("New piece", type="primary"):
                _reset_job()
                st.rerun()
            if session is not None and st.button("Reuse this brief",
                                                 help="Back to the brief form with this job's brief and tone filled in"):
                _restore_brief(session)
                _reset_job()
                st.rerun()

# ---------------------------------------------------------------- knowledge base


@st.fragment
def kb_review(client_id: str, client_dir: Path, reviewer: str) -> None:
    """The fact table. A fragment, so ticking boxes and filtering don't rerun the whole app."""
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    entries = [e for e in entries if e.get("status") != "excluded"]
    if not entries:
        st.info("No facts yet. Add documents or a fact in the **Add facts** tab.")
        return
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.get("status", "pending")] = counts.get(e.get("status", "pending"), 0) + 1
    statuses = [s for s in FACT_STATUS if counts.get(s)]

    c1, c2, c3 = st.columns([3, 2, 2])
    with c1:
        show = st.pills("Status", statuses, selection_mode="multi", key=f"pills-{client_id}",
                        default=[s for s in statuses if s != "rejected"],
                        format_func=lambda s: f"{FACT_STATUS[s]} · {counts[s]}")
    sources = sorted({e.get("source_doc", "") for e in entries})
    from_docs = c2.multiselect("Source", sources, placeholder="All sources")
    query = c3.text_input("Search facts", placeholder="Words in the fact")
    shown = [
        e for e in entries
        if e.get("status", "pending") in show
        and (not from_docs or e.get("source_doc") in from_docs)
        and query.lower() in e.get("claim", "").lower()
    ]
    if not shown:
        st.caption("No facts match. Pick a status above or clear the filters.")
        return

    select_all = st.checkbox(f"Select all {len(shown)} shown", key=f"all-{client_id}")
    rows = [{"select": select_all, "id": e.get("id"), "status": FACT_STATUS.get(e.get("status", "pending")),
             **{k: e.get(k) for k in ("claim", "source_doc", "location", "verified_at", "confirmed_by")}}
            for e in shown]
    edited = st.data_editor(
        rows, width="stretch", hide_index=True, key=f"kb-{client_id}-{select_all}",
        disabled=[k for k in rows[0] if k != "select"],
        column_config={"select": st.column_config.CheckboxColumn("Select", width="small"),
                       "id": None,
                       "status": st.column_config.TextColumn("Status", width="small"),
                       "claim": st.column_config.TextColumn("Fact", width="large"),
                       "source_doc": "Source", "location": "Where", "verified_at": "Decided on",
                       "confirmed_by": "By"},
    )
    status_of = {e.get("id"): e.get("status", "pending") for e in shown}
    chosen = [r["id"] for r in edited if r["select"]]
    deleted = [i for i in chosen if status_of.get(i) == "rejected"]
    no_name = not reviewer.strip()

    with st.container(horizontal=True, vertical_alignment="center"):
        if st.button(f"Verify ({len(chosen)})", type="primary", disabled=not chosen or no_name):
            kb_verify.approve(client_dir, chosen, reviewer.strip())
            st.session_state["flash"] = f"Verified {len(chosen)} fact(s)."
            st.rerun()
        with st.popover(f"Delete ({len(chosen)})", disabled=not chosen or no_name):
            st.markdown(f"Delete {len(chosen)} fact(s)? Drafts stop using them at once.")
            st.caption("They stay listed as Deleted, so a re-compile won't propose them again. "
                       "You can send them back to review later.")
            if st.button("Yes, delete", type="primary", key=f"del-{client_id}"):
                kb_verify.reject(client_dir, chosen, reviewer.strip())
                st.session_state["flash"] = f"Deleted {len(chosen)} fact(s)."
                st.rerun()
        if deleted and st.button(f"Back to review ({len(deleted)})", help="Un-delete: returns them as Needs review"):
            kb_verify.restore(client_dir, deleted)
            st.session_state["flash"] = f"Sent {len(deleted)} fact(s) back for review."
            st.rerun()
        st.caption("Enter your name in the sidebar to verify or delete." if no_name else
                   "Only verified facts can appear in drafts.")


with tab_kb:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    all_entries = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    set_aside = [e for e in all_entries if e.get("status") == "excluded"]
    pending_n = sum(1 for e in all_entries if e.get("status", "pending") == "pending")
    review_tab, aside_tab, add_tab, profile_tab = st.tabs([
        f"Review facts ({pending_n} to review)" if pending_n else "Review facts",
        f"Set aside ({len(set_aside)})", "Add facts", "Client profile"])

    with review_tab:
        kb_review(client_id, client_dir, reviewer)

    with aside_tab:
        if not set_aside:
            st.caption("Nothing set aside.")
        else:
            st.caption("Style rules, audience notes and reference material found in the documents. They never "
                       "reach a draft and don't need review. Send one back if it's really a claim about the client.")
            aside_rows = [{"select": False, "id": e["id"], "kind": e.get("kind", ""), "claim": e.get("claim"),
                           "why": e.get("exclusion_reason") or "", "source_doc": e.get("source_doc")}
                          for e in sorted(set_aside, key=lambda e: e.get("kind", ""))]
            aside_edited = st.data_editor(
                aside_rows, width="stretch", hide_index=True, key=f"aside-{client_id}",
                disabled=["id", "kind", "claim", "why", "source_doc"],
                column_config={"select": st.column_config.CheckboxColumn("Select", width="small"),
                               "id": None, "kind": "Kind",
                               "claim": st.column_config.TextColumn("Text", width="large"),
                               "why": st.column_config.TextColumn("Why set aside", width="medium"),
                               "source_doc": "Source"},
            )
            back = [r["id"] for r in aside_edited if r["select"]]
            if st.button(f"Send back for review ({len(back)})", disabled=not back):
                kb_verify.restore(client_dir, back)
                st.session_state["flash"] = f"Sent {len(back)} fact(s) back for review."
                st.rerun()

    with add_tab:
        fact_col, docs_col = st.columns(2, gap="large")
        with fact_col, st.container(border=True):
            st.markdown("**Add a fact you know first-hand**")
            with st.form(f"add-fact-{client_id}", clear_on_submit=True, border=False):
                claim = st.text_input("Fact, as a plain sentence")
                source = st.text_input("Source", placeholder="e.g. confirmed by the founder on the 2026-09-20 call")
                verified_now = st.checkbox("Mark verified now (you are the source)", value=True)
                added = st.form_submit_button("Add fact", disabled=not reviewer.strip())
            if not reviewer.strip():
                st.caption("Enter your name in the sidebar to add a fact.")
            if added:
                if claim.strip() and source.strip():
                    kb_add.add_claim(client_dir, claim.strip(), source.strip(), reviewer.strip(), verified=verified_now)
                    st.session_state["flash"] = "Fact added."
                    st.rerun()
                else:
                    st.error("Both the fact and its source are required.")
        with docs_col, st.container(border=True):
            st.markdown("**Add documents and compile**")
            uploads = st.file_uploader("Source documents", accept_multiple_files=True, key=f"up-{client_id}",
                                       type=DOC_TYPES, label_visibility="collapsed")
            docs_dir = client_dir / "knowledge_base" / "documents"
            replacing = [f.name for f in uploads or [] if (docs_dir / Path(f.name).name).exists()]
            if replacing:
                st.warning(f"Will replace the existing document(s): {', '.join(replacing)}")
            if st.button("Save and compile", type="primary", disabled=not uploads):
                _save_uploads(uploads, client_dir)
                st.session_state["flash"] = _compile(client_dir)
                st.rerun()
            st.caption("Only claims about the client go to review; style rules and reference material are set aside.")

    with profile_tab:
        style_guide = client_dir / "style_guide.md"
        guide_text = style_guide.read_text(encoding="utf-8") if style_guide.exists() else ""
        if guide_text and "DRAFT" in guide_text.splitlines()[0]:
            st.warning("This profile is still marked DRAFT: review the style guide, tone presets and constraints "
                       f"in clients/{client_id}/ before the first real job.")
        left, right = st.columns([3, 2], gap="large")
        with left:
            st.markdown("**Style guide**")
            if guide_text:
                with st.container(border=True, height=420):
                    st.markdown(guide_text)
            else:
                st.caption("No style guide.")
        with right:
            st.markdown(f"**Tone presets ({len(profile.tone_presets)})**")
            for t in profile.tone_presets:
                st.markdown(f"**{t.name}**: {t.description}  \n:gray[_“{t.sample_line}”_]")
            constraints_path = client_dir / "constraints.yaml"
            if constraints_path.exists():
                data = yaml.safe_load(constraints_path.read_text(encoding="utf-8")) or {}
                st.markdown("**Do not say**")
                st.markdown(", ".join(f"`{t}`" for t in data.get("do_not_say") or []) or ":gray[none]")
                if data.get("do_not_frame"):
                    st.markdown("**Do not frame**")
                    for rule in data["do_not_frame"]:
                        st.markdown(f"- {rule}")
            docs_dir = client_dir / "knowledge_base" / "documents"
            docs = sorted(p.name for p in docs_dir.iterdir() if not p.name.startswith(".")) if docs_dir.exists() else []
            with st.expander(f"Source documents ({len(docs)})"):
                for name in docs:
                    st.markdown(f"- {name}")

# ---------------------------------------------------------------- jobs


@st.fragment
def jobs_view(client_id: str) -> None:
    """A fragment, so picking a row in the history doesn't rerun the whole app."""
    records, unreadable = stats.load_records(OUTPUT_ROOT, client_id)
    if not records:
        st.info("No jobs for this client yet. Start one in the Generate tab.")
        return
    summary = stats.summarize(records)

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v:.0%}"

    cols = st.columns(4)
    cols[0].metric("Jobs run", summary["attempted"],
                   help="Excludes jobs still waiting for an angle or micro-copy choice")
    cols[1].metric("Completed", pct(summary["completion_rate"]), help="Reached a package, of jobs run")
    cols[2].metric("First pass", pct(summary["first_pass_rate"]), help="Completed with no gate retries")
    cols[3].metric("Needed a human", summary["awaiting_human"] + summary["failed"])

    gates = sorted(set(summary["retries_by_gate"]) | set(summary["failures_by_gate"]),
                   key=lambda g: -summary["retries_by_gate"].get(g, 0))
    if gates:
        with st.expander(f"Where drafts get stuck: most retries at `{gates[0]}`"):
            st.dataframe(
                [{"gate": g, "retries": summary["retries_by_gate"].get(g, 0),
                  "final failures": summary["failures_by_gate"].get(g, 0)} for g in gates],
                hide_index=True,
            )
    if unreadable:
        st.caption(f"Skipped {len(unreadable)} unreadable record(s): {', '.join(unreadable)}")

    st.markdown("**History** :gray[· click a row to open the job]")
    event = st.dataframe(
        [{"created (UTC)": f"{r.created_at:%Y-%m-%d %H:%M}", "type": _label(r.format) if r.format else "—",
          "status": JOB_STATUS.get(r.status, r.status), "tone": r.tone.preset_name if r.tone else "—",
          "retries": sum(r.retry_count.values()), "brief": r.brief_summary, "job": r.job_id} for r in records],
        width="stretch", hide_index=True, key=f"hist-{client_id}", on_select="rerun", selection_mode="single-row",
    )
    if not event.selection.rows:
        return
    r = records[event.selection.rows[0]]
    with st.container(border=True):
        st.markdown(f"**Job `{r.job_id}`** · {JOB_STATUS.get(r.status, r.status)}  \n:gray[{r.brief_summary}]")
        session_path = OUTPUT_ROOT / "jobs" / r.job_id / "session.json"
        if r.status == "awaiting_selection" and session_path.exists():
            session = run_job.load_session(OUTPUT_ROOT, r.job_id)
            if session.tone is None:
                st.caption("This job started before jobs could be resumed. Start a new piece instead.")
            elif st.button("Resume in Generate", type="primary", key=f"resume-{r.job_id}"):
                _reset_job()
                st.session_state.update(job_id=r.job_id, tone_choice=session.tone, angle_pick=session.angle_index,
                                        step="microcopy" if session.microcopy_menu else "angle",
                                        flash=f"Resumed job {r.job_id}: open the Generate tab.")
                st.rerun()
        render_output(r.job_id, "jobs")


with tab_jobs:
    jobs_view(client_id)

# ---------------------------------------------------------------- new client

with tab_new:
    st.caption("Scaffolds from clients/_template. The profile drafted from documents is marked DRAFT: "
               "review the style guide, tone presets and constraints before the first real job.")
    with st.form("new-client"):
        c1, c2 = st.columns(2)
        new_id = c1.text_input("Client id", placeholder="acme", help="Lowercase letters, digits, - or _. Can't be changed later.")
        company = c2.text_input("Company name")
        industry = c1.text_input("Industry")
        website = c2.text_input("Website")
        docs = st.file_uploader("Source documents (decks, brand guides, past content, notes)",
                                accept_multiple_files=True, type=DOC_TYPES)
        draft_it = st.checkbox("Draft style guide, tone presets and constraints from the documents", value=True)
        compile_it = st.checkbox("Extract knowledge-base facts from the documents", value=True)
        submitted = st.form_submit_button("Create client", type="primary")

    if submitted:
        try:
            target = client_setup.init_client(new_id.strip(), CLIENTS_ROOT)
            client_setup.set_identity(target, company.strip(), industry.strip(), website.strip())
        except (ValueError, FileExistsError) as exc:
            st.error(str(exc))
        else:
            # The client exists from here on, so a failed model step is reported, not raised: a retry
            # would otherwise hit "already exists".
            notes = [f"Created {target.name}."]
            if docs:
                _save_uploads(docs, target)
                if draft_it:
                    try:
                        with st.spinner("Drafting the client profile from the documents…"):
                            client_setup.draft_profile(target, target / "knowledge_base" / "documents")
                    except Exception as exc:  # noqa: BLE001 - shown to the human, who fills the profile by hand
                        notes.append(f"Profile draft failed ({exc}); fill it in by hand.")
                if compile_it:
                    try:
                        notes.append(_compile(target))
                    except Exception as exc:  # noqa: BLE001 - retry from Knowledge base -> Add facts
                        notes.append(f"Compile failed ({exc}); retry from Knowledge base.")
            elif draft_it or compile_it:
                notes.append("No documents uploaded, so nothing was drafted or compiled.")
            notes.append("Review it in the Knowledge base tab.")
            _reset_job()
            st.session_state.update(pending_client=target.name, flash=" ".join(notes))
            st.rerun()
