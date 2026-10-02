# Quickstart

## Zero-setup run (synthetic client)

```bash
./setup.sh
source .venv/bin/activate
python -m pipeline.cli run --client exemplar --brief tests/fixtures/sample_brief.yaml
```

No real client data and no API key are required beyond an authenticated `claude` CLI (`claude
/login` if you haven't already) -- `exemplar` is a fully synthetic company on `example.com`, the
same pattern `neural-pnotp-gtm`'s `exemplar` tenant uses.

The job package lands at `output/jobs/<job_id>/package.json`: the draft, micro-copy options
(titles/hooks/CTAs), a provenance manifest mapping every claim used to its knowledge-base source,
and a compliance report showing which gates passed. `output/jobs/<job_id>/job_record.json` is the
audit trail: which stages ran, which gates passed/failed/skipped, and any human touchpoint.

## Running the test suite

```bash
python -m pytest tests/ -q
```

OCR-dependent tests skip gracefully (with a clear reason) on a machine without the `tesseract`
and `poppler` system binaries installed -- install with `brew install tesseract poppler` if you
want that coverage; everything else (including text-layer PDF extraction) runs regardless.

## Adding a new client

1. **Scaffold**: `python -m pipeline.cli init-client acme --from-docs ~/Downloads/acme-materials/`
   Copies the blank template and, if `--from-docs` is given, drafts a first-pass style guide,
   tone presets, and do-not-say list from whatever's in that folder -- clearly marked DRAFT.
   Without `--from-docs`, you get the bare empty template to fill in by hand.
2. **Review the draft profile** (one human pass): edit `clients/acme/style_guide.md`,
   `tone_presets.yaml`, `constraints.yaml` -- approve or correct what the agent proposed.
3. **Drop source documents** (PDFs, DOCX, text, images -- decks, brand guidelines, past content)
   into `clients/acme/knowledge_base/documents/`.
4. **Compile**: `python -m pipeline.cli kb-compile --client acme` -- reads every document into
   three layers: **facts** (new `kb_index.json` entries tagged with source and page, extracted a
   few pages at a time so long decks are read to the end), **context** (the document itself, in
   sections with a role and summary, under `knowledge_base/context/`, which the writer reads for
   direction) and **plan** (any planned pieces -- article lists, keyword clusters, email series --
   proposed into `content_plan.yaml`).
5. **Verify the delta**: `python -m pipeline.cli kb-verify --client acme` -- shows only new or
   changed facts, not the whole knowledge base. Approving stamps `provenance.json` and unblocks
   the client for drafting. Use `--approve-all` to skip the interactive per-entry prompt.
6. **Approve the plan**: `python -m pipeline.cli plan --client acme`, then
   `plan-approve --client acme --all` (or name item ids). Edit titles, keywords or order in
   `content_plan.yaml` directly if the extraction got them wrong.
7. **Run a job**: `python -m pipeline.cli run --client acme --brief <path>`. To write a plan item,
   put `plan_item_id: plan-xxxxxx` in the brief; the angles, outline and draft are all on that
   item and its keyword.

Steps 1-6 are a one-time cost per client (4-6 repeat only when new documents show up, and only on
the delta) -- a second brief for the same client skips straight to step 7.

## Briefs

```yaml
client_id: acme
goal: "Write the plan piece on comparison keywords."
audience: "Quality engineers at mid-size manufacturers."
format: blog_post
word_range: {min: 1200, max: 2000}   # style_lint fails a body more than 10% outside it
plan_item_id: plan-ab12cd            # optional: write this content plan item
notes: "Lead with the repeatability spec."
```

An older brief with `target_word_count: N` still loads, read as N ±15%.

**Preflight**: before any angle is generated, the brief is checked for a format that doesn't
match the goal ("two blog posts" as a `social_post`) and for asks that contradict the client's
framing or do-not-say rules -- briefs that can never pass. `run` stops with the problems; fix the
brief, or rerun with `--accept-preflight` to proceed (the problems are recorded on the job and the
writer is told the client's rules win).

## When a job escalates

A revision edits the failed draft with every failing gate's findings, so most jobs settle in one
or two rounds. When a gate still fails after its budget, the job stops with the draft in
`last_failed_draft.json` and every round in `job_record.json` (`rounds`) and `drafts/rev-N.json`.
Finish it yourself with `python -m pipeline.cli finish --job <id> --body-file edited.txt --by you`
(house style, length and do-not-say still apply; the judged gates are not re-run), and keep a good
result as a voice example with `approve --job <id>`.

Every judge note is logged to `output/lessons/<client>.jsonl`. Once notes recur across jobs,
`suggest-rules --client acme` proposes style-guide, framing or banned-phrase additions, and
`add-rule --client acme --target style_guide --rule "..."` adopts one.

## The local UI

```bash
pip install -e ".[ui]"
streamlit run ui/app.py
```

**Generate**: pick an approved item from the content plan (or none), a client and content type,
write the prompt (audience, word range and must-follow notes are pre-filled from that client's
last brief of the same type in `local_briefs/<client>/`, or from the plan item), pick a tone
preset or a custom one. If preflight finds a problem with the brief, fix it or press Proceed
anyway. Then choose an angle, then the micro-copy (options marked ⚠ would fail the micro-copy
gate). The result shows with copy buttons, a PDF download, the revision history, and an Approve
as a voice example button; an escalated job offers Finish it yourself. **Knowledge base**: review
the content plan (Approve / Reject), see each source document's role and summary, tick facts and
Verify or Delete them (enter your name in
the sidebar first; it is recorded as `confirmed_by`), add a fact you know first-hand, or upload
documents and compile. Deleted facts are kept as `rejected`, so a later compile won't propose
them again. Compiling labels every extracted statement; only claims about the client
enter the review queue. Style rules, audience notes and reference material are set aside with a
reason (restorable from the tab), and meeting notes or personal details are never stored. **Jobs**: history and success rates per
client, and Learn from the judges (suggest and adopt rules). **New client**: the same steps as the
CLI below (scaffold, draft profile from documents, compile), then review in Knowledge base.

## Picking an angle or tone from the CLI

`run` defaults to angle candidate 0, the client's first tone preset, and the first unflagged
micro-copy option per field. Override the first two:

```bash
python -m pipeline.cli run --client acme --brief brief.yaml --angle-index 1 --tone case_study
```
