Write the full draft described by the working spec below. Follow the outline and style checklist
exactly. Use only the facts provided in claims_to_use — if the brief wants something said that
isn't supported by those facts, leave it out rather than inventing a supporting fact.

If the spec includes a "revision_instruction" field, this is a revision: "previous_draft" is the
draft that failed. Edit that draft rather than starting again. Fix every issue the instruction
lists and keep the sentences it doesn't mention, unless a fix needs them changed.

Write the body only: no subject line, preheader or title (those are picked separately as micro-copy).
Leave no template text or bracketed placeholders. If the greeting needs the recipient's name, use
the merge field {{first_name}}.

Respond as a single JSON object: {"body": "the full draft text", "claims_used": ["kb-id-1"]}
(claims_used should be the subset of claims_to_use the body actually references).
