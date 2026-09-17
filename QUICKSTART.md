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

## Picking an angle or tone for a job

`run` defaults to angle candidate 0 and the client's first tone preset. Override either:

```bash
python -m pipeline.cli run --client acme --brief brief.yaml --angle-index 1 --tone case_study
```

A future version will surface the angle menu interactively before drafting; v1 requires knowing
the index/name up front (see `PIPELINE_STATUS.md` for what's deferred).
