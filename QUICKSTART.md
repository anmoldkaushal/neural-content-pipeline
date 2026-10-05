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
4. **Compile**: `python -m pipeline.cli kb-compile --client acme` -- extracts facts from every
   document, drafts new `kb_index.json` entries tagged with their source, diffs against what's
   already verified.
5. **Verify the delta**: `python -m pipeline.cli kb-verify --client acme` -- shows only new or
   changed facts, not the whole knowledge base. Approving stamps `provenance.json` and unblocks
   the client for drafting. Use `--approve-all` to skip the interactive per-entry prompt.
6. **Run a job**: `python -m pipeline.cli run --client acme --brief <path>`.

Steps 1-5 are a one-time cost per client (steps 4-5 repeat only when new documents show up, and
only on the delta) -- a second brief for the same client skips straight to step 6.

## The local UI

```bash
pip install -e ".[ui]"
streamlit run ui/app.py
```

**Generate**: pick a client and content type, write the prompt (audience, word count and
must-follow notes are pre-filled from that client's last brief of the same type in
`local_briefs/<client>/`), pick a tone preset or a custom one. Then choose an angle, then the
micro-copy (options marked ⚠ would fail the micro-copy gate), or skip it with "Skip micro-copy and write
draft". A finished job has "Add micro-copy" (or "Change micro-copy"): options fit the draft, and your
picks are gated before they join the package and PDF. The result shows with copy buttons
and a PDF download; "Reuse this brief" refills the form for another run. **Knowledge base**: tick facts and Verify or Delete them (enter your name in
the sidebar first; it is recorded as `confirmed_by`; a deleted fact can be sent back to review), add a fact you know first-hand, or upload
documents and compile. Deleted facts are kept as `rejected`, so a later compile won't propose
them again. Compiling labels every extracted statement; only claims about the client
enter the review queue. Style rules, audience notes and reference material are set aside with a
reason (restorable from the tab), and meeting notes or personal details are never stored. **Jobs**: history and success rates per client; click a row to open a job, or resume one left at the angle or micro-copy step. **New client**: the same steps as the
CLI below (scaffold, draft profile from documents, compile), then review in Knowledge base.

## Picking an angle or tone from the CLI

`run` defaults to angle candidate 0, the client's first tone preset, and the first unflagged
micro-copy option per field. Override the first two:

```bash
python -m pipeline.cli run --client acme --brief brief.yaml --angle-index 1 --tone case_study
```
