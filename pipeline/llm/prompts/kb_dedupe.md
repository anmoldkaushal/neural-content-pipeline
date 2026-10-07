You are cleaning up a client's knowledge base before review. Below are facts extracted from the
client's documents: CONFIRMED facts a person or earlier review already accepted, and NEW facts
waiting for review. Each line is "[id] fact (source document)".

1. Duplicates: find NEW facts that state the same thing as another fact (NEW or CONFIRMED), even
   in different words. Group them. "keep" is the id to keep: a CONFIRMED id if the group has one,
   otherwise the clearest, most complete NEW fact. "same" lists the other NEW ids in the group.
   Never put a CONFIRMED id in "same". Optionally give "claim": one wording for the kept fact
   that covers everything the group says, using only what the group says. Omit "claim" when the
   kept wording is already right, and always omit it when "keep" is CONFIRMED. Only group facts
   that really say the same thing; facts on the same topic that add different details are not
   duplicates.
2. Conflicts: find facts (NEW or CONFIRMED) that cannot all be true: different dates, prices,
   numbers, names or terms for the same thing. Give their ids and one short sentence on the clash.

Respond as a single JSON object (empty lists are fine):
{"duplicates": [{"keep": "kb-1", "same": ["kb-2", "kb-3"], "claim": "..."}], "conflicts": [{"ids": ["kb-4", "kb-9"], "issue": "..."}]}
