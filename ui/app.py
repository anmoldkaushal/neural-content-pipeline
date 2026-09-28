"""Local UI over the pipeline: `streamlit run ui/app.py`.

Generate walks the three run_job phases with a human choice between each (angle -> micro-copy ->
draft + gates); Knowledge base and Jobs are read-only views over clients/ and output/jobs/.
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
from pipeline.output.pdf_export import field_label  # noqa: E402
from pipeline.schemas import Brief, TonePreset  # noqa: E402
from pipeline.stages import kb_add, kb_compile, kb_verify, revise, tone_select  # noqa: E402

CLIENTS_ROOT = REPO_ROOT / "clients"
OUTPUT_ROOT = REPO_ROOT / "output"
BRIEFS_ROOT = REPO_ROOT / "local_briefs"
CUSTOM_TONE = "Custom tone…"
WRITE_OWN = "Write my own…"
STATUS_ICON = {"passed": "✅", "failed": "❌", "skipped": "⚠️"}

st.set_page_config(page_title="Content Pipeline", layout="wide")


def _clients() -> list[str]:
    return sorted(p.name for p in CLIENTS_ROOT.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))


def _save_uploads(files, client_dir: Path) -> None:
    docs_dir = client_dir / "knowledge_base" / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        (docs_dir / Path(f.name).name).write_bytes(f.getbuffer())


def _compile(client_dir: Path) -> None:
    with st.spinner("Extracting facts from documents (one model call per document)…"):
        entries, warnings = kb_compile.compile_kb(client_dir)
    pending = sum(1 for e in entries if e.status == "pending")
    st.session_state["flash"] = f"Compiled: {pending} fact(s) pending review." + (
        f" Warnings: {'; '.join(warnings)}" if warnings else "")


def _reset_job() -> None:
    for key in ("job_id", "step", "tone_choice"):
        st.session_state.pop(key, None)


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
        st.success("Provenance verified")
    else:
        st.error("Provenance blocks drafting")
        for p in problems:
            st.caption(f"• {p}")
    reviewer = st.text_input("Your name", key="reviewer",
                             help="Recorded as confirmed_by when you verify or delete facts")

if flash := st.session_state.pop("flash", None):
    st.toast(flash)


def render_output(job_id: str) -> None:
    """A finished (or blocked) job: copyable text, gate results, PDF download."""
    output_dir = OUTPUT_ROOT / "jobs" / job_id
    record = job_record.load(output_dir)
    package_path = output_dir / "package.json"

    if package_path.exists():
        package = json.loads(package_path.read_text(encoding="utf-8"))
        for field, text in (package.get("microcopy_selected") or {}).items():
            st.markdown(f"**{field_label(field)}**")
            st.code(text, language=None, wrap_lines=True)
        st.markdown("**Body**")
        st.code(package["draft"]["body"], language=None, wrap_lines=True)

        pdf_path = output_dir / "package.pdf"
        cols = st.columns(2)
        if pdf_path.exists():
            cols[0].download_button(
                "Download PDF", pdf_path.read_bytes(), file_name=f"{record.client_id}-{job_id}.pdf",
                mime="application/pdf", key=f"pdf-{job_id}",
            )
        cols[1].download_button(
            "Download package.json", package_path.read_bytes(), file_name=f"{job_id}-package.json",
            mime="application/json", key=f"json-{job_id}",
        )
        other = {f: c for f, c in (package.get("microcopy") or {}).items() if c}
        if other:
            with st.expander("All micro-copy options"):
                for field, cands in other.items():
                    st.markdown(f"**{field_label(field)}**")
                    for c in cands:
                        st.markdown(f"- {c['text']}  _({c.get('strategy', '')})_")
    else:
        failed_path = output_dir / "last_failed_draft.json"
        if failed_path.exists():
            body = json.loads(failed_path.read_text(encoding="utf-8")).get("body", "")
            st.warning("No package: this is the last draft that failed a gate.")
            st.code(body, language=None, wrap_lines=True)

    if record.gate_results:
        st.markdown("**Gates**")
        for g in record.gate_results:
            st.markdown(f"{STATUS_ICON.get(g.status.value, '')} `{g.gate_name}`: {g.detail}")
            for item in g.flagged_items:
                st.caption(f"  • {item}")
    if record.human_touchpoints:
        with st.expander("Notes for human review"):
            for note in record.human_touchpoints:
                st.markdown(f"- {note}")


tab_generate, tab_kb, tab_jobs, tab_new = st.tabs(["Generate", "Knowledge base", "Jobs", "New client"])

# ---------------------------------------------------------------- generate

with tab_generate:
    step = st.session_state.get("step", "brief")

    if step == "brief":
        formats = brief_defaults.known_formats(BRIEFS_ROOT, client_id)
        fmt = st.selectbox("Content type", formats, key=f"fmt-{client_id}")
        d = brief_defaults.defaults_for(BRIEFS_ROOT, client_id, fmt)
        k = f"{client_id}-{fmt}"  # widget keys per client+format so switching re-prefills

        goal = st.text_area("Prompt", d["goal"], key=f"goal-{k}", height=110,
                            placeholder="What should this piece do?")
        with st.expander("Modifiers (pre-filled from the client's last brief of this type)", expanded=True):
            audience = st.text_area("Audience", d["audience"], key=f"aud-{k}", height=80)
            c1, c2 = st.columns([1, 3])
            words = c1.number_input("Target words", 20, 4000, int(d["target_word_count"] or 150), key=f"wc-{k}")
            angle_hint = c2.text_input("Angle hint (optional)", key=f"hint-{k}")
            notes = st.text_area("Must follow", d["notes"], key=f"notes-{k}", height=90)
            st.caption(
                f"Enforced by the gates regardless: house style list, {len(profile.do_not_say)} do-not-say "
                f"term(s), {len(profile.do_not_frame)} framing rule(s), verified KB facts only."
            )

        st.subheader("Tone")
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

        if st.button("Generate angles", type="primary", disabled=not goal.strip()):
            try:
                if tone_pick == CUSTOM_TONE:
                    tone = tone_select.custom_tone(custom_desc, custom_name, custom_sample)
                    if save_it:
                        try:
                            tone_select.save_preset(client_dir, TonePreset(
                                name=tone.preset_name, description=custom_desc.strip(), sample_line=custom_sample.strip()))
                            tone.ad_hoc = False
                        except ValueError as exc:
                            st.warning(f"Not saved: {exc}")
                else:
                    tone = tone_select.select_tone(profile, tone_pick)
                brief = Brief(client_id=client_id, goal=goal.strip(), audience=audience.strip(), format=fmt,
                              target_word_count=int(words), angle_hint=angle_hint.strip() or None,
                              notes=notes.strip() or None)
                with st.spinner("Checking provenance and generating angles…"):
                    session = run_job.start(client_id, brief, CLIENTS_ROOT, OUTPUT_ROOT)
                st.session_state.update(job_id=session.job_id, step="angle", tone_choice=tone)
                st.rerun()
            except (ValueError, ProvenanceError, run_job.JobBlocked) as exc:
                st.error(str(exc))

    elif step == "angle":
        job_id = st.session_state["job_id"]
        session = run_job.load_session(OUTPUT_ROOT, job_id)
        st.caption(f"Job `{job_id}` · {session.brief.format} · tone: {st.session_state['tone_choice'].preset_name}")
        st.subheader("Choose an angle")
        labels = [f"**{a.headline}** — {a.pitch}" for a in session.angles]
        idx = st.radio("Angle", range(len(labels)), format_func=lambda i: labels[i], label_visibility="collapsed")
        with st.expander("Structure of this angle"):
            for s in session.angles[idx].structure:
                st.markdown(f"- {s}")
        c1, c2 = st.columns([1, 5])
        if c1.button("Back"):
            _reset_job()
            st.rerun()
        if c2.button("Generate micro-copy menu", type="primary"):
            with st.spinner("Generating micro-copy options…"):
                run_job.build_microcopy_menu(job_id, idx, st.session_state["tone_choice"], CLIENTS_ROOT, OUTPUT_ROOT)
            st.session_state["step"] = "microcopy"
            st.rerun()

    elif step == "microcopy":
        job_id = st.session_state["job_id"]
        session = run_job.load_session(OUTPUT_ROOT, job_id)
        st.caption(f"Job `{job_id}` · angle: {session.angles[session.angle_index].headline}")
        st.subheader("Pick the micro-copy")
        st.caption("⚠ marks options the micro-copy gate would reject. Pick another or write your own.")
        selected: dict[str, str] = {}
        for field, cands in session.microcopy_menu.items():
            st.markdown(f"**{field_label(field)}**")
            if field in session.microcopy_errors:
                st.caption(f"No options generated: {session.microcopy_errors[field]}")
            labels = [f"{'⚠ ' if c.flags else ''}{c.text}  _({c.strategy})_" for c in cands] + [WRITE_OWN]
            default = next((i for i, c in enumerate(cands) if not c.flags), len(cands))
            pick = st.radio(field, range(len(labels)), index=default, format_func=lambda i, l=labels: l[i],
                            key=f"mc-{job_id}-{field}", label_visibility="collapsed")
            if pick == len(cands):
                selected[field] = st.text_input(f"Your {field_label(field).lower()}", key=f"own-{job_id}-{field}")
            else:
                selected[field] = cands[pick].text
                for flag in cands[pick].flags:
                    st.caption(f"⚠ {flag}")
        c1, c2 = st.columns([1, 5])
        if c1.button("Back"):
            st.session_state["step"] = "angle"
            st.rerun()
        if c2.button("Write draft and run gates", type="primary"):
            try:
                with st.spinner("Drafting and running gates (this is the slow step)…"):
                    run_job.execute(job_id, selected, CLIENTS_ROOT, OUTPUT_ROOT)
            except (run_job.JobBlocked, revise.RetryBudgetExceeded) as exc:
                st.session_state["blocked"] = str(exc)
            st.session_state["step"] = "output"
            st.rerun()

    elif step == "output":
        job_id = st.session_state["job_id"]
        if blocked := st.session_state.pop("blocked", None):
            st.error(f"Stopped for a human: {blocked}")
        render_output(job_id)
        if st.button("New piece", type="primary"):
            _reset_job()
            st.rerun()

# ---------------------------------------------------------------- knowledge base

with tab_kb:
    index_path = client_dir / "knowledge_base" / "kb_index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    by_status: dict[str, int] = {}
    for e in entries:
        by_status[e.get("status", "pending")] = by_status.get(e.get("status", "pending"), 0) + 1

    cols = st.columns(5)
    for col, (status, label) in zip(cols, (("pending", "Needs review"), ("verified", "Verified"),
                                           ("excluded", "Set aside"), ("stale", "Stale"), ("rejected", "Deleted"))):
        col.metric(label, by_status.get(status, 0))

    reviewable = sorted(s_ for s_ in by_status if s_ != "excluded")
    sources = sorted({e.get("source_doc", "") for e in entries if e.get("status") != "excluded"})
    c1, c2, c3 = st.columns([2, 2, 3])
    show = c1.multiselect("Status", reviewable, default=[x for x in reviewable if x != "rejected"])
    from_docs = c2.multiselect("Source", sources, placeholder="All sources")
    query = c3.text_input("Search facts")
    shown = [
        e for e in entries
        if e.get("status", "pending") in show
        and (not from_docs or e.get("source_doc") in from_docs)
        and query.lower() in e.get("claim", "").lower()
    ]
    select_all = st.checkbox(f"Select all {len(shown)} shown", key=f"all-{client_id}")
    rows = [
        {"select": select_all, **{k: e.get(k) for k in ("id", "status", "claim", "source_doc", "location", "verified_at", "confirmed_by")}}
        for e in shown
    ]
    edited = st.data_editor(
        rows, width="stretch", hide_index=True, key=f"kb-{client_id}-{select_all}",
        disabled=[k for k in (rows[0] if rows else {}) if k != "select"],
        column_config={"select": st.column_config.CheckboxColumn("✓", width="small"),
                       "claim": st.column_config.TextColumn(width="large")},
    )
    chosen = [r["id"] for r in edited if r["select"]]
    c1, c2, c3 = st.columns([1, 1, 3])
    no_name = not reviewer.strip()
    if c1.button(f"Verify ({len(chosen)})", type="primary", disabled=not chosen or no_name):
        kb_verify.approve(client_dir, chosen, reviewer.strip())
        st.session_state["flash"] = f"Verified {len(chosen)} fact(s)."
        st.rerun()
    if c2.button(f"Delete ({len(chosen)})", disabled=not chosen or no_name):
        kb_verify.reject(client_dir, chosen, reviewer.strip())
        st.session_state["flash"] = f"Deleted {len(chosen)} fact(s) (kept as rejected so re-compiling won't re-add them)."
        st.rerun()
    c3.caption("Enter your name in the sidebar to verify or delete." if no_name else
               "Verified facts are the only ones drafts may use. Deleted facts are kept as 'rejected'.")

    set_aside = [e for e in entries if e.get("status") == "excluded"]
    if set_aside:
        with st.expander(f"Set aside, not for copy ({len(set_aside)})"):
            st.caption("Style rules, audience notes and reference material found in the documents. They never "
                       "reach a draft and don't need review. Send one back if it's really a claim about the client.")
            aside_rows = [{"select": False, "id": e["id"], "kind": e.get("kind", ""), "claim": e.get("claim"),
                           "why": e.get("exclusion_reason") or "", "source_doc": e.get("source_doc")}
                          for e in sorted(set_aside, key=lambda e: e.get("kind", ""))]
            aside_edited = st.data_editor(
                aside_rows, width="stretch", hide_index=True, key=f"aside-{client_id}",
                disabled=["id", "kind", "claim", "why", "source_doc"],
                column_config={"select": st.column_config.CheckboxColumn("✓", width="small"),
                               "claim": st.column_config.TextColumn(width="large"),
                               "why": st.column_config.TextColumn(width="medium")},
            )
            back = [r["id"] for r in aside_edited if r["select"]]
            if st.button(f"Send back for review ({len(back)})", disabled=not back):
                kb_verify.restore(client_dir, back)
                st.session_state["flash"] = f"Sent {len(back)} fact(s) back for review."
                st.rerun()

    with st.expander("Add a fact you know first-hand"):
        with st.form(f"add-fact-{client_id}", clear_on_submit=True):
            claim = st.text_input("Fact, as a plain sentence")
            source = st.text_input("Source", placeholder="e.g. confirmed by the founder on the 2026-09-20 call")
            verified_now = st.checkbox("Mark verified now (you are the source)", value=True)
            if st.form_submit_button("Add fact", disabled=no_name):
                if claim.strip() and source.strip():
                    kb_add.add_claim(client_dir, claim.strip(), source.strip(), reviewer.strip(), verified=verified_now)
                    st.session_state["flash"] = "Fact added."
                    st.rerun()
                else:
                    st.error("Both the fact and its source are required.")

    with st.expander("Add documents and compile"):
        uploads = st.file_uploader("Source documents", accept_multiple_files=True, key=f"up-{client_id}",
                                   type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"])
        if st.button("Save and compile", disabled=not uploads):
            _save_uploads(uploads, client_dir)
            _compile(client_dir)
            st.rerun()

    docs_dir = client_dir / "knowledge_base" / "documents"
    docs = sorted(p.name for p in docs_dir.iterdir() if not p.name.startswith(".")) if docs_dir.exists() else []
    with st.expander(f"Source documents ({len(docs)})"):
        for name in docs:
            st.markdown(f"- {name}")
    style_guide = client_dir / "style_guide.md"
    if style_guide.exists():
        with st.expander("Style guide"):
            st.markdown(style_guide.read_text(encoding="utf-8"))
    with st.expander(f"Tone presets ({len(profile.tone_presets)})"):
        for t in profile.tone_presets:
            st.markdown(f"**{t.name}**: {t.description}  \n_“{t.sample_line}”_")
    constraints_path = client_dir / "constraints.yaml"
    if constraints_path.exists():
        with st.expander("Constraints"):
            data = yaml.safe_load(constraints_path.read_text(encoding="utf-8")) or {}
            st.markdown("**Do not say:** " + ", ".join(f"`{t}`" for t in data.get("do_not_say") or []))
            for rule in data.get("do_not_frame") or []:
                st.markdown(f"- {rule}")

# ---------------------------------------------------------------- jobs

with tab_jobs:
    records, unreadable = stats.load_records(OUTPUT_ROOT, client_id)
    summary = stats.summarize(records)

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v:.0%}"

    cols = st.columns(4)
    cols[0].metric("Jobs run", summary["attempted"])
    cols[1].metric("Completed", pct(summary["completion_rate"]), help="Reached a package, of jobs attempted")
    cols[2].metric("First pass", pct(summary["first_pass_rate"]), help="Completed with no gate retries")
    cols[3].metric("Needed a human", summary["awaiting_human"] + summary["failed"])

    gates = sorted(set(summary["retries_by_gate"]) | set(summary["failures_by_gate"]))
    if gates:
        st.markdown("**Where drafts get stuck**")
        st.dataframe(
            [{"gate": g, "retries": summary["retries_by_gate"].get(g, 0),
              "final failures": summary["failures_by_gate"].get(g, 0)} for g in gates],
            hide_index=True,
        )
    if unreadable:
        st.caption(f"Skipped {len(unreadable)} unreadable record(s): {', '.join(unreadable)}")

    st.markdown("**History**")
    st.dataframe(
        [{"created (UTC)": f"{r.created_at:%Y-%m-%d %H:%M}", "job": r.job_id, "type": r.format or "—",
          "status": r.status, "tone": r.tone.preset_name if r.tone else "—",
          "retries": sum(r.retry_count.values()), "brief": r.brief_summary} for r in records],
        width="stretch", hide_index=True,
    )
    if records:
        pick = st.selectbox("Open a job", [r.job_id for r in records],
                            format_func=lambda j: next(f"{j} · {r.status} · {r.format or '—'}" for r in records if r.job_id == j))
        render_output(pick)

# ---------------------------------------------------------------- new client

with tab_new:
    st.caption("Scaffolds from clients/_template. The profile drafted from documents is marked DRAFT: "
               "review the style guide, tone presets and constraints before the first real job.")
    with st.form("new-client"):
        c1, c2 = st.columns(2)
        new_id = c1.text_input("Client id", placeholder="acme  (lowercase, digits, - or _)")
        company = c2.text_input("Company name")
        industry = c1.text_input("Industry")
        website = c2.text_input("Website")
        docs = st.file_uploader("Source documents (decks, brand guides, past content, notes)",
                                accept_multiple_files=True, type=["pdf", "docx", "txt", "md", "png", "jpg", "jpeg"])
        draft_it = st.checkbox("Draft style guide, tone presets and constraints from the documents", value=True)
        compile_it = st.checkbox("Extract knowledge-base facts from the documents", value=True)
        submitted = st.form_submit_button("Create client", type="primary")

    if submitted:
        try:
            target = client_setup.init_client(new_id.strip(), CLIENTS_ROOT)
            client_setup.set_identity(target, company.strip(), industry.strip(), website.strip())
            if docs:
                _save_uploads(docs, target)
                if draft_it:
                    with st.spinner("Drafting the client profile from the documents…"):
                        client_setup.draft_profile(target, target / "knowledge_base" / "documents")
                if compile_it:
                    _compile(target)
            st.session_state["pending_client"] = target.name
            st.session_state.setdefault("flash", f"Created {target.name}. Review its facts in the Knowledge base tab.")
            _reset_job()
            st.rerun()
        except (ValueError, FileExistsError) as exc:
            st.error(str(exc))
