You are drafting a first-pass client profile from the documents provided (brand materials, past
content, website copy). This is explicitly a DRAFT for a human to review and correct — do not
present it as final, and do not fabricate anything not implied by the documents.

Produce: a short style guide summary (voice description, formatting conventions), 2-3 named tone
presets each with a one-line description and a short sample line, and two separate constraint
lists (empty if nothing applies):

- `do_not_say`: literal words or short phrases to ban outright (a competitor name, a banned
  claim like "FDA approved", a brand-forbidden word). Each entry must be the bare term itself --
  nothing else -- because it is checked by exact substring match. Do not write an explanation
  into this list; a sentence describing a rule will never match anything.
- `do_not_frame`: positioning or framing rules that can be violated without ever using a banned
  word -- an anti-persona to disqualify, a category the brand refuses to be placed in, an
  urgency/scarcity tactic to avoid, a claim that is unconfirmed and must not be stated as final.
  These are checked by an independent judge reading the draft's meaning, so write them as full
  sentences describing the rule.

Also draft the brief defaults a writer starts from: `audience` (who the client writes to, in a
sentence or two) and `must_follow` (the standing instructions every piece must obey, one per line).
Draft 2-4 `icps` (ideal customer profiles: the distinct kinds of reader the client writes to),
using only what the documents show or clearly imply. Each has a short snake_case `name`, a one-
sentence `summary`, `roles` (job titles), `company` (size, sector, region), and lists of `pains`,
`cares_about` and `objections`. Leave a list empty rather than inventing entries. Set
`default_tone` to the name of the tone preset above that suits that reader best, or omit it. These
are targeting context, never claims: do not copy product facts into them.

And list `review_flags`: anything in the documents that conflicts or that you had to guess, as one
sentence each, for the human to resolve. Empty if nothing.

Respond as a single JSON object:
{"style_guide_summary": "...", "tone_presets": [{"name": "...", "description": "...", "sample_line": "..."}], "do_not_say": [], "do_not_frame": [], "brief_defaults": {"audience": "...", "must_follow": "..."}, "icps": [{"name": "...", "summary": "...", "roles": [], "company": "...", "pains": [], "cares_about": [], "objections": [], "default_tone": "..."}], "review_flags": []}
