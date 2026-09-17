You are compiling a client's knowledge base from a source document. Read the provided document
text and extract discrete, checkable facts — not summaries, not opinions, not marketing copy.
A good fact is a single sentence a human could confirm or deny by checking the source.

For each fact, note which part of the source document it came from (a short location hint —
section heading, or "paragraph N" if there are no headings).

Respond as a single JSON array of objects: [{"claim": "...", "location": "..."}, ...].
Extract at most 15 facts. Skip anything that is vague, promotional, or not independently checkable.
