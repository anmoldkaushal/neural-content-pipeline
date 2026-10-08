Write the full draft described by the working spec below, for the client whose rules follow.

- Follow the outline and style checklist. Land the body inside the word range.
- Follow every client rule below; the draft is checked against each of them after you finish.
- State facts about the client only from VERIFIED FACTS. If the brief wants something said that
  no verified fact supports, leave it out rather than inventing support. General explanation is
  fine; specific figures, statistics, research findings and anything about the client's offering,
  people, places, prices, schedule or results need a verified fact.
- CLIENT CONTEXT (if present) tells you what to write about, for whom, and with which keywords and
  positioning. Use it for direction; never state anything from it as fact unless it is also a
  verified fact.
- If a content plan item is given, the piece is that item: cover its topic and use its primary
  keyword naturally.
- If "known_conflicts" is present, a human chose to proceed past a clash between the brief and
  the client's rules: the client's rules win.
- Write the body only: no subject line, preheader, title or sign-off (the sign-off is added
  afterwards). Leave no template text or bracketed placeholders. If the greeting needs the
  recipient's name, use the merge field {{first_name}}.
- If the working spec has "sequence", you are writing one email of a sequence. Also write its
  subject_line (a few plain words a person would open) and preheader (one short line that extends
  the subject, never repeats it), held to the same client rules as the body. Open, close and phrase
  it differently from every email in "emails_already_written": same link, a different invitation.

Respond as a single JSON object: {"body": "the full draft text", "claims_used": ["kb-id-1"]}
(claims_used is every verified fact id the body actually relies on). For one email of a sequence,
add "subject_line" and "preheader" to that object.
