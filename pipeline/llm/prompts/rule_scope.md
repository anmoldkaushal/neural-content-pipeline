You are deciding which of a client's standing writing rules apply to one piece of content.

Some rules apply to every piece ("the call to action is 'Apply for a discovery call'", "never say
'bufo'", "make no medical treatment claims"). Others apply only to one kind of piece: they name a
type, topic or format ("medicine articles need a safety note", "in emails, keep to one link",
"when writing about a retreat location, ..."). A rule that forbids or requires something whenever
it comes up is a rule for every piece: it costs nothing when the topic doesn't come up.

For each numbered rule, give its scope:
- "always": a rule for every piece.
- "conditional": a rule for one kind of piece. Then say whether THIS piece is that kind.

A piece is that kind only when that is what the piece is mainly about or what format it is. A
passing mention doesn't count: a guide to a retreat that mentions sacred medicine once is not a
medicine article; a guide to what 5-MeO-DMT is, its risks or its history is. If you cannot tell,
say it applies.

Respond as a single JSON object:
{"rules": [{"n": 1, "scope": "always" or "conditional", "applies": true or false, "why": "one short line"}]}
