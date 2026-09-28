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

Respond as a single JSON object:
{"style_guide_summary": "...", "tone_presets": [{"name": "...", "description": "...", "sample_line": "..."}], "do_not_say": [], "do_not_frame": []}
