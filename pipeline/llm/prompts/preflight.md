You are checking a content brief against a client's rules before anyone writes a word. A draft
that follows the brief will be failed by an independent reviewer for any rule it breaks, so a
brief that asks for something a rule forbids (or forbids something a rule requires) can never
produce a passing draft.

List only real conflicts: places where doing what the brief says would break a rule, or where a
rule requires something the brief tells the writer to leave out. Tension that a careful writer
can satisfy both ways is not a conflict.

For each conflict give the brief's words, the rule, and one sentence on how a writer would satisfy
the rule while honouring as much of the brief as possible.

Respond as a single JSON object:
{"conflicts": [{"brief_says": "...", "rule": "...", "resolution": "..."}]}
(an empty list when there are none).
