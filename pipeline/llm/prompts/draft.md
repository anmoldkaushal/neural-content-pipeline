Write the full draft described by the working spec below. Follow the outline and style checklist
exactly. Use only the facts provided in claims_to_use — if the brief wants something said that
isn't supported by those facts, leave it out rather than inventing a supporting fact.

If the spec includes a "revision_instruction" field, this is a revision of a previous draft that
failed a specific gate — fix exactly what it describes, do not rewrite unrelated parts.

Respond as a single JSON object: {"body": "the full draft text", "claims_used": ["kb-id-1"]}
(claims_used should be the subset of claims_to_use the body actually references).
