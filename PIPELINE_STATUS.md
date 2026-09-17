# Pipeline status

## Stages (in order)

| Stage | File | Why it exists |
|---|---|---|
| Provenance gate | `pipeline/kb/provenance.py` | Hard-blocks a job before any drafting if the client's facts aren't confirmed+sourced+dated. Ported from `verify_tenant.py`'s refuse-loudly behavior. |
| Intake | `pipeline/stages/intake.py` | Catches mechanical brief/KB gaps before spending an LLM call. |
| Angle menu | `pipeline/stages/angle_menu.py` | 2-3 cheap candidates so a bad direction never costs a full draft+gate cycle. |
| Tone select | `pipeline/stages/tone_select.py` | A dropdown-level decision from the client's pre-approved presets, not a generation. |
| Brief synthesis | `pipeline/stages/brief_synthesis.py` | Compiles one targeted working spec instead of handing the drafter four raw documents. |
| Draft | `pipeline/stages/draft.py` | The one expensive, single-commit generation pass. |
| Self-check | `pipeline/stages/self_check.py` | Cheap same-pass reflection before the independent gates run. |
| Micro-copy | `pipeline/stages/microcopy.py` | Parallel lane: diverse title/hook/CTA candidates, cheap because they're short. |
| Gates (style/constraints/entailment/voice) | `pipeline/gates/` | Independent passes -- never the same call that wrote the draft, to avoid self-grading bias. |
| Revise loop | `pipeline/stages/revise.py` | Bounded retry (default 2/gate); a third failure escalates to a human with the specific failing gate, never loops silently. |
| Package | `pipeline/stages/package.py` | The deliverable is the bundle (draft + provenance + compliance report), not just prose. |

## Gates, and the incident/decision each one traces to

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

## Deferred to v2 (and why)

- **Mutation-kill testing** (`neural-pnotp-gtm/waterfall/tools/mutation_kill.py` pattern) --
  proves a gate can actually fail, not just that it asserts it would. Real value, but a
  second-order tool; add once the gates themselves are stable.
- **Risk-tiered auto-approval** for the final human gate -- v1 always produces a package for
  human approval. Tiering (skip review when everything's green and low-risk) needs real usage
  data to calibrate against before it's safe to build.
- **A UI / interactive angle-and-tone picker** -- CLI only for v1; `run` takes `--angle-index`
  and `--tone` up front rather than surfacing the menu interactively.
- **A real database backend** -- filesystem JSON/YAML per client is enough at this scale; a DB is
  a v2 problem if/when client count or concurrent-job count makes it one.
