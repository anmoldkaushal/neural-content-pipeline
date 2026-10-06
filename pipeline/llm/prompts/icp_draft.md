You are drafting ideal customer profiles (ICPs) for a client from the documents and audience notes
below. This is a DRAFT for a human to review and correct: do not fabricate anything the sources
don't show or clearly imply.

Propose 2-4 distinct ICPs, the different kinds of reader the client writes to, skipping any the
client already has. Each has a short snake_case `name`, a one-sentence `summary`, `roles` (job
titles), `company` (size, sector, region), and lists of `pains`, `cares_about` and `objections`.
Leave a list empty rather than inventing entries. Set `default_tone` to the listed tone preset that
suits that reader best, or omit it. ICPs are targeting context, never claims: do not copy product
facts into them.

Respond as a single JSON object:
{"icps": [{"name": "...", "summary": "...", "roles": [], "company": "...", "pains": [], "cares_about": [], "objections": [], "default_tone": "..."}]}
