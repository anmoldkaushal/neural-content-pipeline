You are reviewing what independent judges have repeatedly said about one client's drafts, to
propose rules that would stop the writer making the same mistakes. Only propose a rule when the
same kind of problem appears in notes from more than one job; ignore one-offs. Do not repeat a
rule the client already has, and do not propose anything the notes don't show.

Each rule goes to exactly one target:
- "style_guide": a writing habit to adopt or avoid (rhythm, structure, a recurring construction
  like stacked "not X, but Y" sentences or teaser transitions). One plain sentence.
- "do_not_frame": a positioning or framing rule a judge reads for meaning. One full sentence
  starting "Do not".
- "banned_phrase": a short literal phrase that keeps appearing and is always wrong for this client.
  The bare phrase only.

Respond as a single JSON array, at most 6 items, most frequent first:
[{"target": "style_guide", "rule": "...", "why": "one line: what keeps going wrong", "evidence": "how many notes / jobs"}]
(an empty array when nothing recurs).
