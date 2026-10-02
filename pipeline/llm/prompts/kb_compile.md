You are compiling a client's knowledge base from a source document (possibly one part of a longer
document; its pages or part are named in the header). The knowledge base exists for
one purpose: the facts a copywriter may state about the client in published content. Read the
provided document text and extract discrete, checkable statements, one sentence each, that a
human could confirm or deny by checking the source. Not summaries, opinions or marketing copy.

Label every statement with exactly one kind:
- "claim": about the client's own company, offering, people, history, format, pricing, dates or
  results, stated in a way that could appear in the client's copy. Only these are reviewed.
- "style": a wording, terminology or framing rule (use this term, avoid that one, keep X
  confidential, how visitors should feel).
- "audience": who the client targets or sells to (personas, ICP, demographics, regions).
- "reference": competitors, market data, or general knowledge not specific to the client
  (science, history, third-party organisations or public figures).
- "internal": project or meeting notes, action items, who attended, commercial terms between the
  client and its agency, tooling, research-method metadata, test entries.
- "personal": anything about a named private individual's health, finances, or private quotes.

When a claim comes from a meeting, state the fact itself ("The journey runs ten days"), not who
said it. When two parts of the document disagree, extract both as claims and say which part each
came from in the location.

For each statement, note where in the document it came from (a section heading, or "paragraph N"
if there are no headings).

Respond as a single JSON array: [{"claim": "...", "location": "...", "kind": "claim"}, ...].
Extract at most 25 statements from this part. Skip anything vague or promotional. Planned content
(topic lists, keyword clusters, article plans) is catalogued separately: do not restate it here.
