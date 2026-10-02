# Pipeline status

## Stages (in order)

| Stage | File | Why it exists |
|---|---|---|
| Provenance gate | `pipeline/kb/provenance.py` | Hard-blocks a job before any drafting if the client's facts aren't confirmed+sourced+dated. Ported from `verify_tenant.py`'s refuse-loudly behavior. |
| Intake | `pipeline/stages/intake.py` | Catches mechanical brief/KB gaps before spending an LLM call. |
| Angle menu | `pipeline/stages/angle_menu.py` | 2-3 cheap candidates so a bad direction never costs a full draft+gate cycle. |
| Tone select | `pipeline/stages/tone_select.py` | A dropdown-level decision from the client's pre-approved presets. A human may type a one-off tone (recorded as `ad_hoc` on the job and judged by voice_critic) or ask for one suggestion; it becomes a preset only when a human saves it. |
| Micro-copy menu | `pipeline/stages/microcopy.py` | Runs after the angle is chosen and before drafting: per-format fields (`formats` in `config/pipeline_config.yaml`), each option pre-flagged by the deterministic lint so the human picks copy the gates will accept. |
| Brief synthesis | `pipeline/stages/brief_synthesis.py` | Compiles one targeted working spec instead of handing the drafter four raw documents. |
| Draft | `pipeline/stages/draft.py` | The one expensive, single-commit generation pass. |
| Self-check | `pipeline/stages/self_check.py` | Cheap same-pass reflection before the independent gates run. |
| Gates (style/constraints/entailment/voice) | `pipeline/gates/` | Independent passes -- never the same call that wrote the draft, to avoid self-grading bias. |
| Revise loop | `pipeline/stages/revise.py` | Bounded retry (default 2/gate); a third failure escalates to a human with the specific failing gate, never loops silently. |
| Package | `pipeline/stages/package.py` | The deliverable is the bundle (draft + provenance + compliance report), not just prose. |

The orchestrator runs in three phases (`start` -> `build_microcopy_menu` -> `execute` in
`pipeline/run_job.py`) with a human choice between each; state between phases is
`output/jobs/<id>/session.json`. `run` chains all three for the CLI.

## Gates, and the incident/decision each one traces to

- **`microcopy_lint`** -- the picked micro-copy against the same house list and do-not-say terms
  as the body. Runs before drafting and blocks instead of retrying: redrafting the body can never
  fix a subject line. The judged gates also read the draft with the picked copy on top.
- **`style_lint`** -- deterministic, zero-tolerance. Ported from
  `neural-pnotp-gtm/waterfall/writing_rules.py`: a client may only ADD to the banned-word lists,
  never remove from the base list.
- **`client_constraints`** -- per-client do-not-say list. Same zero-tolerance pattern,
  client-specific.
- **`entailment`** -- every claim used in a draft must trace to a *verified* KB entry. The
  highest-priority gate: same reasoning as `longevity-science-daily`'s `entailment_gate.py` -- an
  unsupported claim is the costliest failure mode.
- **`voice_critic`** -- LLM judge, independent call. Reports SKIPPED (never PASSED) if the
  transport is unavailable -- same discipline as `llm_judge.py`.

## Local UI (v2)

`streamlit run ui/app.py`: pick a client, content type, prompt + pre-filled modifiers and a tone;
choose an angle, then the micro-copy; read, copy or download (PDF) the result. Knowledge base
verifies, deletes (as `rejected`) and adds facts, and compiles uploaded documents; Jobs shows
history with completion and first-pass rates and retries per gate, and resumes a job left mid-way; New client scaffolds a client.

## Deferred (and why)

- **Mutation-kill testing** (`neural-pnotp-gtm/waterfall/tools/mutation_kill.py` pattern) --
  proves a gate can actually fail, not just that it asserts it would. Real value, but a
  second-order tool; add once the gates themselves are stable.
- **Risk-tiered auto-approval** for the final human gate -- v1 always produces a package for
  human approval. Tiering (skip review when everything's green and low-risk) needs real usage
  data to calibrate against before it's safe to build.
- **A real database backend** -- filesystem JSON/YAML per client is enough at this scale; a DB is
  a v2 problem if/when client count or concurrent-job count makes it one.
