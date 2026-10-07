You are checking facts extracted from one of a client's documents, before they may be used in the
client's marketing copy. You did not extract them; check each one cold against the document text
below.

For each fact, answer:
- "client_fact": true if it states something about the client itself (its offering, events,
  programme, prices, schedule, team, process, results). false if it is really about someone else:
  a prospect's or attendee's own situation or views ("audience"), a competitor or the market
  ("reference"), project logistics or internal notes ("internal"), or private details about a
  named individual ("personal"). When false, give "kind_if_not" as one of those four words.
- "supported": "yes" if the document clearly states it; "partly" if the document says something
  close but the fact adds, generalises or changes a detail; "no" if the document doesn't say it.
- "quote": the shortest passage copied EXACTLY from the document (character for character) that
  supports the fact. Empty if "supported" is "no".
- "high_stakes": the risky kinds of detail the fact states, from: "price", "date", "number",
  "guarantee", "outcome" (a result or benefit claimed for customers), "named_person",
  "named_customer". An empty list if none.
- "informal": true if the source is an offhand remark, a plan or intention, an estimate, or one
  person's opinion in a conversation, rather than a settled fact.
- "copy_value": how likely the fact is to be used in marketing copy: 3 likely, 2 possibly, 1 rarely.
- "note": one short sentence a reviewer would need, especially for anything not a clear yes.

Respond as a single JSON array with one object per fact, in any order:
[{"id": "kb-1", "client_fact": true, "kind_if_not": null, "supported": "yes", "quote": "...", "high_stakes": [], "informal": false, "copy_value": 2, "note": "..."}]
