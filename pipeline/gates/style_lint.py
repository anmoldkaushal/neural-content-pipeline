"""Canonical, zero-tolerance style/format gate. Ported from
neural-pnotp-gtm/waterfall/writing_rules.py's extension seam: a client may only ADD to the
banned-word/phrase lists via its own ClientProfile.banned_words / banned_phrases, never remove
from the base lists below. This file fails the run — it does not warn."""
from __future__ import annotations

import re
from typing import Optional

from pipeline import config
from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus, WordRange

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


def banned_lists(profile: ClientProfile) -> tuple[list[str], list[str]]:
    """The effective (base + client additions) banned words and phrases -- what this gate checks,
    and what the drafter is shown up front so it isn't graded on a list it never saw."""
    banned_words = list(BASE_BANNED_WORDS)
    for w in profile.banned_words:
        if w.lower() not in {b.lower() for b in banned_words}:
            banned_words.append(w)

    banned_phrases = list(BASE_BANNED_PHRASES)
    for p in profile.banned_phrases:
        if p.lower() not in {b.lower() for b in banned_phrases}:
            banned_phrases.append(p)
    return banned_words, banned_phrases


def find_violations(text: str, profile: ClientProfile) -> list[str]:
    """Banned words/phrases and dashes -- the length-independent checks, reused by
    microcopy_lint.py so short copy is held to the same house rules as the body."""
    banned_words, banned_phrases = banned_lists(profile)

    flagged: list[str] = []
    flagged += [f"banned word: {w!r}" for w in _find_hits(text, banned_words)]
    flagged += [f"banned phrase: {p!r}" for p in _find_hits(text, banned_phrases)]

    if "—" in text or "–" in text:
        flagged.append("em-dash or en-dash present")
    return flagged


def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


_GREETING = re.compile(r"^(hi|hello|hey|dear)\b.{0,40},$", re.IGNORECASE)


def body_sentences(text: str) -> list[str]:
    """The body's sentences, without a greeting line ("Hi {{first_name}},"). Paragraph breaks end a
    sentence too, so a closing link with no full stop still counts as one."""
    lines = [line.strip() for line in text.strip().splitlines()]
    if lines and _GREETING.match(lines[0]):
        lines = lines[1:]
    out: list[str] = []
    for para in re.split(r"\n\s*\n", "\n".join(lines)):
        para = " ".join(para.split())
        out += [s for s in re.split(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z\"'(])", para) if s.strip()]
    return out


def length_violations(word_count: int, word_range: Optional[WordRange], tolerance: float) -> list[str]:
    """The brief's range, with tolerance, inside the absolute house floor and ceiling. Each
    message says how far off the draft is, so a revision knows how much to add or cut."""
    flagged: list[str] = []
    if word_count < MIN_WORDS:
        flagged.append(f"too short: {word_count} words (house minimum {MIN_WORDS})")
    if word_count > MAX_WORDS:
        flagged.append(f"too long: {word_count} words (house maximum {MAX_WORDS})")
    if word_range is not None and not flagged:
        if word_count < word_range.min * (1 - tolerance):
            flagged.append(
                f"too short for the brief: {word_count} words, range {word_range.label()} "
                f"(add about {word_range.min - word_count})"
            )
        elif word_count > word_range.max * (1 + tolerance):
            flagged.append(
                f"too long for the brief: {word_count} words, range {word_range.label()} "
                f"(cut about {word_count - word_range.max})"
            )
    return flagged


def run(
    draft: Draft,
    profile: ClientProfile,
    word_range: Optional[WordRange] = None,
    tolerance: Optional[float] = None,
    sentence_max: Optional[int] = None,
) -> GateResult:
    flagged = find_violations(draft.body, profile)
    tolerance = config.word_range_tolerance() if tolerance is None else tolerance
    flagged += length_violations(count_words(draft.body), word_range, tolerance)
    if sentence_max and (n := len(body_sentences(draft.body))) > sentence_max:
        flagged.append(f"too many sentences: {n}, at most {sentence_max} (merge or cut {n - sentence_max})")

    if flagged:
        return GateResult(
            gate_name="style_lint",
            status=GateStatus.FAILED,
            detail=f"{len(flagged)} style violation(s)",
            flagged_items=flagged,
        )
    return GateResult(gate_name="style_lint", status=GateStatus.PASSED, detail="clean")
