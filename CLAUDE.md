# neural-content-pipeline: read this first

Brief + verified client context + client note + style guide -> a gated, reviewed draft. This
repo is deliberately scoped to content creation only: no ideation, no primary research, no
publish/distribute/analyze -- those live elsewhere or arrive as inputs.

- Quickstart: `QUICKSTART.md`. Run a job against the bundled synthetic `exemplar` client with no
  setup beyond an authenticated `claude` CLI.
- What's built vs. deferred, and why each gate exists: `PIPELINE_STATUS.md`.
- The provenance rule: `pipeline/kb/provenance.py`. A fact is not usable in a draft until it is
  confirmed, sourced, and dated in a client's `provenance.json`. Missing provenance is itself a
  failure, not a warning -- `ensure_verified()` refuses loudly, same as
  `neural-pnotp-gtm/waterfall/verify_tenant.py`.
- The house style gate: `pipeline/gates/style_lint.py`. Fails the run, does not warn. A client
  may only ADD to the banned-word/phrase lists via its own profile, never remove from the base
  lists.
- Adding a client: see "Adding a new client" in `QUICKSTART.md` -- `init-client`, drop docs,
  `kb-compile`, `kb-verify`, then `run`.

Nothing here calls the paid Anthropic API by default. `pipeline/llm/transport.py` shells out to
`claude -p`, drawing on your Claude Code subscription; set `CONTENT_JUDGE_TRANSPORT=sdk` to use
the metered API instead.
