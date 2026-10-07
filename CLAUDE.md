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
  `neural-pnotp-gtm/waterfall/verify_tenant.py`. The confirmer is a person or the agent review
  (`pipeline/stages/kb_triage.py`, `confirmed_by: agent:kb-review`), which confirms only low-risk
  facts matched to their source in code and sends everything else to a person. Keep its policy in
  `decide()`, and keep agent confirmations visible (package, PDF) and undoable.
- The house style gate: `pipeline/gates/style_lint.py`. Fails the run, does not warn. A client
  may only ADD to the banned-word/phrase lists via its own profile, never remove from the base
  lists.
- Adding a client: see "Adding a new client" in `QUICKSTART.md` -- `init-client`, drop docs,
  `kb-compile` (runs the agent review), confirm what Needs you, `plan-approve`, then `run`.
- Three knowledge layers, kept apart on purpose: facts (`kb_index.json`, the only thing a draft
  may state), context (`knowledge_base/context/`, document sections the writer reads for
  direction) and plan (`content_plan.yaml`, planned pieces). Context and plan are derived from
  client documents and are gitignored like them.
- The writer is shown every rule it is judged on (`pipeline/kb/rulebook.py`), and a revision edits
  the failed draft with all failing gates' findings (`pipeline/stages/revise.py`). Keep the two in
  step when adding a gate: give it an entry in revise's `_GATE_MEANING`, and put any rule it checks
  into the rulebook.

Nothing here calls the paid Anthropic API by default. `pipeline/llm/transport.py` shells out to
`claude -p`, drawing on your Claude Code subscription; set `CONTENT_JUDGE_TRANSPORT=sdk` to use
the metered API instead.
