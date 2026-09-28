"""Canonical, zero-tolerance style/format gate. Ported from
neural-pnotp-gtm/waterfall/writing_rules.py's extension seam: a client may only ADD to the
banned-word/phrase lists via its own ClientProfile.banned_words / banned_phrases, never remove
from the base lists below. This file fails the run — it does not warn."""
from __future__ import annotations

import re

from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus

# Base, non-overridable list. A client extends this; nothing here is ever removed by a client.
BASE_BANNED_WORDS = [
    "delve", "crucial", "utilize", "leverage", "robust", "seamless", "cutting-edge",
    "game-changer", "revolutionary", "unlock", "elevate", "streamline", "synergy",
    "holistic", "ecosystem", "empower", "paradigm",
]
BASE_BANNED_PHRASES = [
    "it's important to note", "in today's", "this is where", "dive deep", "deep dive",
    "unlock the power of", "take it to the next level",
]

MIN_WORDS = 50
MAX_WORDS = 4000


def _find_hits(body: str, terms: list[str]) -> list[str]:
    lowered = body.lower()
    return [t for t in terms if t.lower() in lowered]


def find_violations(text: str, profile: ClientProfile) -> list[str]:
    """Banned words/phrases and dashes -- the length-independent checks, reused by
    microcopy_lint.py so short copy is held to the same house rules as the body."""
    banned_words = list(BASE_BANNED_WORDS)
    for w in profile.banned_words:
        if w.lower() not in {b.lower() for b in banned_words}:
            banned_words.append(w)

    banned_phrases = list(BASE_BANNED_PHRASES)
    for p in profile.banned_phrases:
        if p.lower() not in {b.lower() for b in banned_phrases}:
            banned_phrases.append(p)

    flagged: list[str] = []
    flagged += [f"banned word: {w!r}" for w in _find_hits(text, banned_words)]
    flagged += [f"banned phrase: {p!r}" for p in _find_hits(text, banned_phrases)]

    if "—" in text or "–" in text:
        flagged.append("em-dash or en-dash present")
    return flagged


def run(draft: Draft, profile: ClientProfile) -> GateResult:
    flagged = find_violations(draft.body, profile)

    word_count = len(re.findall(r"\S+", draft.body))
    if word_count < MIN_WORDS:
        flagged.append(f"too short: {word_count} words (min {MIN_WORDS})")
    if word_count > MAX_WORDS:
        flagged.append(f"too long: {word_count} words (max {MAX_WORDS})")

    if flagged:
        return GateResult(
            gate_name="style_lint",
            status=GateStatus.FAILED,
            detail=f"{len(flagged)} style violation(s)",
            flagged_items=flagged,
        )
    return GateResult(gate_name="style_lint", status=GateStatus.PASSED, detail="clean")
