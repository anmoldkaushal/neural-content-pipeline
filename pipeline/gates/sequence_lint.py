"""Deterministic gate across a sequence: an email may not reuse an earlier email's opening, call to
action, subject line or phrasing, and uses its own anchor word exactly once and no other email's.

Traces to a real five-email drip that passed every per-email gate and still read as
one email sent five times: three emails ended on the identical CTA sentence, two opened "You have
built something real", and lines like "nobody is there to be sold to" recurred. Each email was
judged alone, so nothing could see it. Findings quote the earlier line, so a revision knows exactly
what to change; thresholds live in config (`sequence_lint`)."""
from __future__ import annotations

import re
from typing import Optional

from pipeline import config
from pipeline.gates.style_lint import body_sentences
from pipeline.schemas import Draft, GateResult, GateStatus

_LINK = re.compile(r"https?://\S+")
_WORD = re.compile(r"[a-z0-9']+")
# Words too common to make a shared run meaningful on their own: an n-gram needs two other words.
_STOP = set(
    "a an and are as at be but by can do for from has have i if in is it its me my not of on or so that the "
    "their them there they this to was we were what when where who will with you your yours".split()
)


def _words(text: str) -> list[str]:
    return _WORD.findall(_LINK.sub(" link ", text.lower()))


def _shared_run(a: str, b: str, n: int) -> Optional[str]:
    """The first run of n words both texts contain (with at least two non-trivial words), or None."""
    wa, wb = _words(a), _words(b)
    grams = {tuple(wb[i:i + n]) for i in range(len(wb) - n + 1)}
    for i in range(len(wa) - n + 1):
        gram = tuple(wa[i:i + n])
        if gram in grams and sum(w not in _STOP for w in gram) >= 2:
            return " ".join(gram)
    return None


def same_copy(a: str, b: str) -> bool:
    """Two subject lines (or preheaders) that differ only in case and punctuation."""
    return _words(a) == _words(b)


def opening(body: str) -> str:
    sentences = body_sentences(body)
    return sentences[0] if sentences else ""


def cta(body: str) -> str:
    """The sentence carrying the link, else the last sentence."""
    sentences = body_sentences(body)
    return next((s for s in reversed(sentences) if _LINK.search(s)), sentences[-1] if sentences else "")


def _count(word: str, text: str) -> int:
    return len(re.findall(rf"\b{re.escape(word.lower())}\b", text.lower()))


def find_issues(draft: Draft, earlier: list[Draft], anchor: Optional[str] = None,
                other_anchors: Optional[list[str]] = None) -> list[str]:
    knobs = config.sequence_lint()
    issues: list[str] = []
    copy = f"{draft.subject_line or ''} {draft.preheader or ''} {draft.body}"
    if anchor and (n := _count(anchor, copy)) != 1:
        issues.append(f"anchor word {anchor!r} must appear exactly once in this email; it appears {n} time(s)")
    for other in other_anchors or []:
        if _count(other, copy):
            issues.append(f"{other!r} is another email's anchor word; leave it to that email")

    for prev in earlier:
        k = prev.piece
        if run := _shared_run(opening(draft.body), opening(prev.body), knobs["opening_ngram"]):
            issues.append(f"the opening repeats email {k}'s opening ({run!r}); email {k} opened: "
                          f"\"{opening(prev.body)}\". Open a different way.")
        if run := _shared_run(cta(draft.body), cta(prev.body), knobs["cta_ngram"]):
            issues.append(f"the call to action repeats email {k}'s wording ({run!r}); email {k} closed: "
                          f"\"{cta(prev.body)}\". Keep the same link, phrase the invitation differently.")
        repeats: list[str] = []
        for sentence in body_sentences(draft.body):
            if sentence in (opening(draft.body), cta(draft.body)):
                continue
            if (run := _shared_run(sentence, prev.body, knobs["phrase_ngram"])) and run not in repeats:
                repeats.append(run)
        issues += [f"repeats email {k}'s phrasing: {run!r}. Say something email {k} didn't, or say it differently."
                   for run in repeats[:3]]
        if draft.subject_line and prev.subject_line and same_copy(draft.subject_line, prev.subject_line):
            issues.append(f"the subject line is the same as email {k}'s ({prev.subject_line!r})")
    return issues


def run(draft: Draft, earlier: list[Draft], anchor: Optional[str] = None,
        other_anchors: Optional[list[str]] = None) -> GateResult:
    issues = find_issues(draft, earlier, anchor, other_anchors)
    if issues:
        return GateResult(gate_name="sequence_lint", status=GateStatus.FAILED,
                          detail=f"{len(issues)} repeat/anchor issue(s) across the sequence", flagged_items=issues)
    detail = f"distinct from the {len(earlier)} earlier email(s)" if earlier else "first email"
    return GateResult(gate_name="sequence_lint", status=GateStatus.PASSED, detail=detail)
