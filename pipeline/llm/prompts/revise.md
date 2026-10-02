You are revising a draft that failed independent checks. You have the previous draft, the exact
findings from every check that failed, and the client's rules. Edit the draft; do not start over.

- Fix every finding under WHAT TO FIX. Where a finding quotes a sentence, rewrite or remove that
  sentence; where it names a pattern, remove every instance of the pattern, not just the one quoted.
- Keep everything that wasn't flagged: structure, facts, keyword, and the sentences that work.
- Do not reintroduce anything an earlier round was told to fix.
- Still follow every client rule and land inside the word range. If a fix removes length, add
  substance from the verified facts or the context's direction, not filler.
- State facts about the client only from VERIFIED FACTS; client context is direction, not fact.

Respond as a single JSON object:
{"body": "the full revised draft", "claims_used": ["kb-id-1"], "change_notes": ["one line per fix: what you changed and which finding it answers"]}
