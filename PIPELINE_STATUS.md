# Pipeline status

## Stages (in order)

| Stage | File | Why it exists |
|---|---|---|
| Provenance gate | `pipeline/kb/provenance.py` | Hard-blocks a job before any drafting if the client's facts aren't confirmed+sourced+dated. Ported from `verify_tenant.py`'s refuse-loudly behavior. |
| Preflight | `pipeline/stages/preflight.py` | Runs before a job exists: a format that doesn't match the goal, or brief asks that contradict a client rule. A brief like that fails the same gate on every revision; this finds it for one cheap call instead of a spent retry budget. A human fixes the brief or proceeds (recorded; client rules win). |
| Intake | `pipeline/stages/intake.py` | Catches mechanical brief/KB gaps (and an unapproved plan item) before spending an LLM call. |
| Angle menu | `pipeline/stages/angle_menu.py` | 2-3 cheap candidates so a bad direction never costs a full draft+gate cycle. Sees the content plan item and the most relevant client context. |
| Tone select | `pipeline/stages/tone_select.py` | A dropdown-level decision from the client's pre-approved presets. A human may type a one-off tone (recorded as `ad_hoc` on the job and judged by voice_critic) or ask for one suggestion; it becomes a preset only when a human saves it. |
| Micro-copy menu | `pipeline/stages/microcopy.py` | Runs after the angle is chosen and before drafting: per-format fields (`formats` in `config/pipeline_config.yaml`), each option pre-flagged by the deterministic lint so the human picks copy the gates will accept. |
| Brief synthesis | `pipeline/stages/brief_synthesis.py` | Compiles one targeted working spec instead of handing the drafter four raw documents. |
| Draft | `pipeline/stages/draft.py` | The one full generation pass. The writer gets everything it will be judged on first: the client's rules (`pipeline/kb/rulebook.py`: style guide, tone, banned lists, do-not-say, framing rules, word range), verified facts with their text, the plan item, relevant context, and approved or past examples for voice. |
| Gates (style/constraints/entailment/claims/voice) | `pipeline/gates/` | Independent passes -- never the same call that wrote the draft, to avoid self-grading bias. |
| Revise loop | `pipeline/stages/revise.py` + `draft.revise_draft` | Bounded retry (default 2/gate). A revision EDITS the failed draft, given every failing gate's findings in one round plus what earlier rounds were told; it used to regenerate from the spec told only about the first failing gate. When any gate exhausts its budget the job escalates naming every failing gate; every round is kept (`rounds`, `drafts/rev-N.json`). |
| Finish by hand | `run_job.finish_by_hand` | An escalated draft a human edits is packaged after the deterministic gates; the judged gates aren't re-run (the human is that judgement). |
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
  never remove from the base list. Also enforces the brief's `word_range` (within
  `word_range_tolerance`, default 10%), saying how many words to add or cut.
- **`client_constraints`** -- per-client do-not-say list. Same zero-tolerance pattern,
  client-specific.
- **`entailment`** -- every claim used in a draft must trace to a *verified* KB entry. The
  highest-priority gate: same reasoning as `longevity-science-daily`'s `entailment_gate.py` -- an
  unsupported claim is the costliest failure mode. Checks the claim ids a draft declares.
- **`claims_critic`** -- the judged half of entailment: reads the prose cold against the verified
  facts and fails any statement about the client, and any specific figure or research claim, that
  no fact supports. Needed once the writer reads client context (strategy figures, competitor
  numbers): a fact stated without an id is invisible to `entailment`. Replaces the old self-check
  stage, whose findings were computed and discarded. SKIPPED, never PASSED, if the transport is down.
- **`voice_critic`** -- LLM judge, independent call, given the same style guide and full tone
  descriptions the writer got (it used to see preset names only). Reports SKIPPED (never PASSED)
  if the transport is unavailable -- same discipline as `llm_judge.py`.

## The knowledge layers a writer reads

- **Facts** (`knowledge_base/kb_index.json`) -- what a draft may *state*. Human-verified.
- **Context** (`knowledge_base/context/`, `pipeline/kb/context.py`) -- each source document in
  sections, with a role (strategy, past_content, brand, notes) and summary. The sections most
  relevant to a piece (lexical match, under `context_budget_chars`) go to the angle menu,
  synthesis and writer as *direction*: never a source of facts. Past content stands in as a voice
  reference until the format has an approved example. Gitignored, like the documents.
- **Plan** (`content_plan.yaml`, `pipeline/kb/content_plan.py`) -- planned pieces extracted from
  strategy documents, human-approved, written via a brief's `plan_item_id` and marked drafted.
  Before this, a strategy's topic list had nowhere to live: it isn't a fact, so compile set it
  aside and no prompt ever saw it. Gitignored except the synthetic exemplar's.

Documents are cleaned on ingest (`pipeline/ingest/normalize.py`: letter-spaced headings,
one-word-per-line exports) and compiled a few pages at a time, so a long deck is read to the end.

## Learning across jobs

Judged-gate findings from every round go to `output/lessons/<client>.jsonl`. Once notes recur
across at least two jobs, `suggest-rules` (or Jobs → Learn from the judges) proposes style-guide,
framing or banned-phrase additions; a human adopts each one, additively. Approving a finished or
hand-finished job as a voice example (CLI `approve`, or the button on the result) feeds future
drafts of that format.

## Local UI (v2)

`streamlit run ui/app.py`: pick a content plan item (or none), client, content type, prompt +
pre-filled modifiers (word range) and a tone; resolve any preflight problem; choose an angle, then
the micro-copy; read, copy or download (PDF) the result, see its revision rounds, approve it as a
voice example, or finish an escalated draft by hand. Knowledge base reviews the content plan,
shows each document's role and summary, verifies, deletes (as `rejected`) and adds facts, and
compiles uploaded documents; Jobs shows history with completion and first-pass rates, retries per
gate, and rule suggestions from judge notes; New client scaffolds a client.

## Deferred (and why)

- **Mutation-kill testing** (`neural-pnotp-gtm/waterfall/tools/mutation_kill.py` pattern) --
  proves a gate can actually fail, not just that it asserts it would. Real value, but a
  second-order tool; add once the gates themselves are stable.
- **Risk-tiered auto-approval** for the final human gate -- v1 always produces a package for
  human approval. Tiering (skip review when everything's green and low-risk) needs real usage
  data to calibrate against before it's safe to build.
- **A real database backend** -- filesystem JSON/YAML per client is enough at this scale; a DB is
  a v2 problem if/when client count or concurrent-job count makes it one.
