# Pipeline status

## Stages (in order)

| Stage | File | Why it exists |
|---|---|---|
| Provenance gate | `pipeline/kb/provenance.py` | Hard-blocks a job before any drafting if the client's facts aren't confirmed+sourced+dated. Ported from `verify_tenant.py`'s refuse-loudly behavior. |
| Rule scope | `pipeline/stages/rule_scope.py` | A client's must-follow lines mix rules for every piece with rules for one kind of piece. One cheap call at the start, before preflight and the angle menu, keeps a conditional rule only when the piece is mainly that kind (a passing mention doesn't count); left-out rules and why go on the job record. Fails closed: no verdict keeps the rule. The Modern Ashram Egypt journey guide got the medicine-article rule (safety note, risks, legal, newsletter), grew a medicine section, and failed voice three times for reading like a medicine article. Profile drafting writes such rules as "When the piece is <kind>: <rule>". |
| Preflight | `pipeline/stages/preflight.py` | Runs before a job exists: a format that doesn't match the goal, a goal asking for several pieces from a one-piece format (send it to `email_sequence`), or brief asks that contradict a client rule. A brief like that fails the same gate on every revision; this finds it for one cheap call instead of a spent retry budget. A human fixes the brief or proceeds (recorded; client rules win). |
| Intake | `pipeline/stages/intake.py` | Catches mechanical brief/KB gaps (and an unapproved plan item) before spending an LLM call. |
| Angle menu | `pipeline/stages/angle_menu.py` | 2-3 cheap candidates so a bad direction never costs a full draft+gate cycle. Sees the content plan item and the most relevant client context. |
| Tone select | `pipeline/stages/tone_select.py` | A dropdown-level decision from the client's pre-approved presets. A human may type a one-off tone (recorded as `ad_hoc` on the job and judged by voice_critic) or ask for one suggestion; it becomes a preset only when a human saves it. |
| ICP (optional) | `pipeline/icps.py` | Who the piece is for, picked from the client's reviewed ICPs. Rendered once into `Brief.icp_profile` at `start` and passed to angle, micro-copy, synthesis, draft, tone suggestion, `voice_critic` and `claims_critic`, each time under a "targeting context, not facts" guard. A pain point can shape an angle and be named as the reader's problem in general terms, but never becomes a claim about the client: `claims_critic` sees the reader (with the brief's audience) only to tell a description of the reader's situation from a claim, and still fails a figure, a study or anything specific about the reader's own firm. Entailment is unchanged. |
| Micro-copy menu (optional) | `pipeline/stages/microcopy.py` | Runs after the angle is chosen and before drafting: per-format fields (`formats` in `config/pipeline_config.yaml`), each option pre-flagged by the deterministic lint so the human picks copy the gates will accept. Skippable (`choose_angle` then `execute` with no copy); a finished job can get copy afterwards via `build_microcopy_menu_after` -> `attach_microcopy`, which re-runs `microcopy_lint` and the two judged gates on the draft with the copy on top and leaves the package untouched if any fails. |
| Research (public facts) | `pipeline/stages/research.py` | Before synthesis, one call with web search and fetch (the only one in the pipeline; the writer and judges stay offline) sources the outside-world facts the chosen outline needs: history, culture, background. Code fetches every cited page and requires the quoted passage, and each figure the claim states, to be on it. `decide()` confirms (`agent:research`) a low-stakes fact checked on a reputable domain or on two sites; medical, legal, substance, safety, statistic and contested claims wait for a person; an unchecked quote is kept as rejected so it isn't proposed again. A country's government, university and museum domains count as reputable (`egymonuments.gov.eg`, Egypt's antiquities ministry, didn't match `.gov`, so its Luxor facts waited for a person), and `redecide()` re-applies that rule to facts on file at the next piece. Stored per client in `knowledge_base/public_facts.json` (kind `public`). The Modern Ashram guide to sacred healing in ancient Egypt had no Egypt facts to draw on: the writer padded around its topic and invented a daily schedule to keep its length. `research.enabled` in config switches it off. |
| Brief synthesis | `pipeline/stages/brief_synthesis.py` | Compiles one targeted working spec instead of handing the drafter four raw documents. |
| Draft | `pipeline/stages/draft.py` | The one full generation pass. The writer gets everything it will be judged on first: the client's rules (`pipeline/kb/rulebook.py`: style guide, tone, banned lists, do-not-say, framing rules, word range), verified facts with their text, the plan item, relevant context, and approved or past examples for voice. A sequence format (`email_sequence`, `sequence: N` in config) runs draft -> gates -> revise once per email: the angle is an arc with one step per email, each email is written knowing the ones before it, has its own retry budget, and the package holds every email under its own heading (`package["sequence"]`). A real brief for "drip emails 1 to 5" sent as one 100-200-word email squeezed a five-email arc into one body and failed on every round. A sequence can be written from the content plan (`plan_item_ids`, one approved item per email): the plan fixes each email's topic, order and anchor word, and the angle menu proposes only the thread through them. Brief `notes` apply to every email; `sequence_notes` to the sequence as a whole. Each email is written with its own subject line and preheader (no menu before drafting; a per-email menu offers alternatives on the finished job), and the package adds the client's sign-off (`sender` in client.yaml) and a `sequence.csv` export (optional `utm` on the link, export only). The first real drip run this way had no subject lines and read as one email sent five times. |
| Gates (style/constraints/entailment/claims/voice) | `pipeline/gates/` | Independent passes -- never the same call that wrote the draft, to avoid self-grading bias. |
| Revise loop | `pipeline/stages/revise.py` + `draft.revise_draft` | Bounded retry (default 3 drafts per gate, so two revisions; 2 gave each email in a sequence a single revision while three reviewers pulled at it). A revision EDITS the failed draft, given every failing gate's findings in one round plus what earlier rounds were told, and is told the client's banned terms win over any wording a finding suggests; it used to regenerate from the spec told only about the first failing gate. When any gate exhausts its budget the job escalates naming every failing gate; every round is kept (`rounds`, `drafts/rev-N.json`). |
| Finish by hand | `run_job.finish_by_hand` | An escalated draft a human edits is packaged after the deterministic gates; the judged gates aren't re-run (the human is that judgement). |
| Package | `pipeline/stages/package.py` | The deliverable is the bundle (draft + provenance + compliance report), not just prose. |

The orchestrator runs in three phases (`start` -> `build_microcopy_menu` -> `execute` in
`pipeline/run_job.py`) with a human choice between each; state between phases is
`output/jobs/<id>/session.json`. `run` chains all three for the CLI.

## Gates, and the incident/decision each one traces to

- **`microcopy_lint`** -- the picked micro-copy against the same house list and do-not-say terms
  as the body. Runs before drafting and blocks instead of retrying: redrafting the body can never
  fix a subject line. The judged gates also read the draft with the picked copy on top.
- **Judged micro-copy check** (`run_job.judge_microcopy`) -- before drafting, the three judged gates
  read the picked copy alone, all fields together. A revision rewrites only the body, so copy a
  judge rejects used to fail every round: on 2026-10-20 a landing page spent its whole retry budget
  on a CLI-picked headline that promised a result and a subheadline that repeated it. A rejected
  pick sends the job back to awaiting_selection (pick again), not to a human; the CLI tries the
  next option per field and stops only if none passes. Every option turned down is kept on the job
  (`microcopy_rejected`, shown in the UI) with the judge's reason. This is the only place a single
  piece's picked copy is judged: after drafting, the fact and framing judges read the body alone,
  and the voice judge sees the copy only to catch a body that repeats it. Judging the copy again
  there disagreed with this check and stalled a rerun the same day, flagging copy no revision could
  change. A sequence email's own subject line and preheader are still judged with its body.
  In the UI a person can keep a rejected pick ("Use it anyway", needs their name): the copy isn't
  judged again and the override is recorded on the job, since the human is the judgement here.
- **Sequence email copy** -- a sequence email's own subject line and preheader go through the same
  `microcopy_lint`, plus a check that both exist, but feed the revise loop: the writer wrote them
  with the body, so a revision can fix them.
- **`sequence_lint`** -- deterministic, per email of a sequence: no run of 4 words from an earlier
  email's opening, 5 from its call to action (the link counts as a word), 7 anywhere else, no
  repeated subject line, the plan item's anchor word exactly once and no other email's (thresholds
  in config). A real five-email drip passed every per-email gate and still read as one email five
  times: three identical closing sentences, two identical openings, lines recurring across emails.
  Each email was judged alone, so nothing could see it. The two judged gates now get this email's
  job and the earlier emails (`judged.sequence_block`), not the whole campaign's must-follow, which
  had them asking every email to open on the turning point and sell the room.
- **`style_lint`** -- deterministic, zero-tolerance. Ported from
  `neural-pnotp-gtm/waterfall/writing_rules.py`: a client may only ADD to the banned-word lists,
  never remove from the base list. Also enforces the brief's `sentence_max` when set (a client's
  drip plan capped emails at five sentences) and the brief's `word_range` (within
  `word_range_tolerance`, default 10%), saying how many words to add or cut. A format nobody
  receives by name (no `greeting: true` in config: blog post, landing page, social post) fails a
  greeting line or any `{{merge field}}`, and the rulebook tells the writer so. On 2026-10-27 a
  Modern Ashram blog post passed every gate opening "Hello {{first_name}},": the writer's greeting
  rule applied to every format and nothing checked it.
- **`client_constraints`** -- per-client do-not-say list. Same zero-tolerance pattern,
  client-specific.
- **`entailment`** -- every claim used in a draft must trace to a *verified* KB entry. The
  highest-priority gate: same reasoning as `longevity-science-daily`'s `entailment_gate.py` -- an
  unsupported claim is the costliest failure mode. Checks the claim ids a draft declares.
- **`cold_reader`** -- judged: reads the piece as a member of the brief's audience who has never
  heard of the client, with no brief, facts or client name, and must say who is writing, what is
  offered and what they are asked to do; "can't tell" on any of the three fails. Every other judge
  reads with the facts, so it already knows the offering and can't notice a piece that never says
  what it is. On 2026-10-20 ten standalone cold emails went out calling the product "the desk" with
  no introduction: each writer had seen email 1, which introduced it. Runs on the formats in
  `cold_reader_formats` (config); for a sequence sent to one reader, email 1 only; for a standalone
  sequence, every email, whose writer now also no longer sees the other emails. The rulebook
  tells the writer the same rule ("Read cold").
- **`claims_critic`** -- the judged half of entailment: reads the prose cold against the verified
  facts and fails any statement about the client, and any specific figure or research claim, that
  no fact supports. Needed once the writer reads client context (strategy figures, competitor
  numbers): a fact stated without an id is invisible to `entailment`. Told a sequence email's anchor word
  is a theme: as an ordinary word it isn't a claim, as a named feature ("the Reset") it is. A real
  drip email escalated after the writer, told only to use "Reset" once, named it as part of the
  event three rounds running. Since 2026-10-20 its findings carry a severity like the other judges
  (only blocking fails; minor rides along as a note) and it is shown the reader: across five M&A
  jobs about half its findings failed the reader's own problem ("searching runs into the evening")
  as an unbacked claim, or failed a finding it called "low risk" itself. Since 2026-10-27 settled
  general knowledge about the world (what and where a place is, what a temple was dedicated to)
  needs no fact, while a specific date, figure or finding still does; and a piece's topic is not a
  claim about the client: only a sentence saying the client goes, offers or includes something is.
  A Modern Ashram guide to Luxor, Karnak and Philae escalated because the judge cut every mention
  of Luxor and Philae (no fact said they were sacred, or that the client went there) and the voice
  judge then failed a guide to three temples that covered one. Replaces the old self-check
  stage, whose findings were computed and discarded. SKIPPED, never PASSED, if the transport is down.
- **`voice_critic`** -- LLM judge, independent call, given the same style guide and full tone
  descriptions the writer got (it used to see preset names only). Reports SKIPPED (never PASSED)
  if the transport is unavailable -- same discipline as `llm_judge.py`. Judged within the client's
  framing rules and the brief's must-follow, so it can't demand what those forbid. It judges voice
  only: it gets the verified facts the writer may state and is told accuracy is claims_critic's job
  (it had failed confirmed facts as "unconfirmed"), and on a revision it gets its own earlier notes
  so it can't ask for the opposite of what it asked before (it had told the writer to stop
  describing the reader, then to open by describing the reader).
- **One rulebook for every judge** (`judged.shared_rules`): voice, framing and claims judges all get the
  client's banned terms (never to suggest one as a fix) and the verified facts the writer may state (a
  matching statement is not 'unconfirmed' unless a rule forbids the topic outright). A real client's job
  failed when the voice judge, never shown the do-not-say list, told the writer to add "not a
  conference or a networking event", and the framing judge failed confirmed agenda facts that the
  voice judge had asked for. The framing judge also reads "don't address X" as broken only by
  addressing X, never as a demand to exclude X.
- **Both judged gates** (`voice_critic`, `client_constraints_critic`) mark each note blocking or
  minor (`pipeline/gates/judged.py`); only a blocking note fails, minor notes ride on the result for
  the human. A note with no severity counts as blocking. A failed revise round gets every failing
  gate's notes plus what earlier rounds were told, and edits the previous draft (`revise.md`).

## The knowledge layers a writer reads

- **Facts** (`knowledge_base/kb_index.json`) -- what a draft may *state*. Confirmed by a person, or
  by the agent review (`pipeline/stages/kb_triage.py`) when the fact is low-risk and matched to its
  source. The review runs after compile because a client with a stack of call notes produced 300+
  near-identical facts to review one by one. It merges duplicates (keeping every source), checks
  each fact against its document with an independent call, then matches the quoted passage and any
  figures in code. A fact with a conflict, a price/date/number/guarantee/outcome/named person from
  one source, informal-only support, or an imperfect match goes to Needs you, ranked; the UI shows
  ten. Agent confirmations are recorded as `agent:kb-review`, listed in the package and PDF, and
  undoable (`kb-review --undo`). entailment and claims_critic still check every draft.
- **Public facts** (`knowledge_base/public_facts.json`, `pipeline/stages/research.py`) -- sourced
  statements about the world, never about the client. They carry their own provenance (source URL,
  quoted passage, date checked, confirmer) rather than a row in `provenance.json`, which stays the
  record for client facts. The writer sees them as PUBLIC FACTS with their source links; the claims
  judge accepts them for history, dates and findings, and marks the unbacked statements it flags as
  about the world, which the job tries to source and re-judges on the same draft before spending a
  revision. Listed in the package (`kind: public`) and PDF; confirm or reject them in the UI's
  Public facts tab.
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

## Client profile review

`pipeline/profile_review.py`: the drafted profile is reviewed per section (style guide, tone
presets, ICPs, banned words, framing rules, brief defaults), Draft -> Reviewed -> Final, in
`clients/<id>/profile_review.json`. Final locks a section; an edit outside the app steps it back
down. Jobs run on a non-final profile, but the job notes and PDF say which sections aren't final.
ICPs (`icps.yaml`: name, summary, roles, company, pains, cares about, objections, optional
default tone) are drafted with the tone presets when a client is added; an ICP's default tone must
name an existing preset. A client added before ICPs gets them from `draft-icps` or the "Draft ICPs
from documents" button, which also reads the audience notes `kb_compile` set aside and only adds
names the client doesn't have yet. Brief defaults (`brief_defaults.yaml`: audience, must-follow,
per-content-type overrides including a word range, custom content types) pre-fill the brief form.

## Local UI (v2)

`streamlit run ui/app.py`: pick a content plan item (or none), client, content type (or a custom
one), prompt + pre-filled modifiers (word range) and a tone; resolve any preflight problem; choose
an angle, then the micro-copy or skip it; read, copy or download (PDF) the result, add or change
micro-copy on a finished job, see its revision rounds, approve it as a voice example, or finish an
escalated draft by hand. Knowledge base reviews facts, set-aside statements and the content plan,
adds facts, compiles uploaded documents, and edits the client profile; Jobs shows history with
completion and first-pass rates, retries per gate, rule suggestions from judge notes, and resumes a
job left mid-way; New client scaffolds a client.

## Deferred (and why)

- **Mutation-kill testing** (`neural-pnotp-gtm/waterfall/tools/mutation_kill.py` pattern) --
  proves a gate can actually fail, not just that it asserts it would. Real value, but a
  second-order tool; add once the gates themselves are stable.
- **Risk-tiered auto-approval** for the final human gate -- v1 always produces a package for
  human approval. Tiering (skip review when everything's green and low-risk) needs real usage
  data to calibrate against before it's safe to build.
- **A real database backend** -- filesystem JSON/YAML per client is enough at this scale; a DB is
  a v2 problem if/when client count or concurrent-job count makes it one.
