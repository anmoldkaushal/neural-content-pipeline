"""Catches a brief that can't pass before anything is drafted. Two checks:

- format fit (deterministic): the goal names a content type the brief's format isn't ("write the
  first two blog posts" sent as an 80-word social_post), or asks for several pieces from a format
  that writes one ("the whole series of drip emails (Email 1 to 5)" sent as one email);
- rule conflicts (one cheap model call): the brief asks for something the client's framing rules
  or do-not-say list forbid ("don't dwell on safety" against a rule requiring a medical caveat).

Both used to surface only after a full draft-and-gate cycle, or never: a brief that contradicts a
client rule fails the same gate on every revision, so the job burned its retry budget on a
failure no revision could fix. Findings go to a human, who fixes the brief or proceeds; proceeding
records them on the job and tells the writer the client's rules win."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pipeline import config
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Brief, ClientProfile

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "preflight.md"

# Words in a goal that name a content type, by the format they mean.
_FORMAT_WORDS = {
    "blog_post": r"\bblogs?\b|\bblog posts?\b|\barticles?\b",
    "email": r"\be-?mails?\b|\bnewsletters?\b",
    "social_post": r"\bsocial posts?\b|\binstagram\b|\bcaptions?\b|\btweets?\b",
    "linkedin_message": r"\blinkedin (?:messages?|dms?|outreach)\b",
    "landing_page": r"\blanding pages?\b",
}


# A sequence format answers to its single-piece name ("emails" fits email_sequence).
_SAME_KIND = {"email_sequence": "email"}
_NUMBER = r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten)"
# A goal that asks for more than one piece.
_SEVERAL = re.compile(
    rf"\b(?:series|sequence|drip|campaign of|set of)\b|\b{_NUMBER}\s+(?:\w+\s+)?(?:e-?mails|posts|messages|articles|blogs)\b"
    rf"|\b(?:e-?mail|post|message)s?\s*#?\d+\s*(?:to|-|–|through)\s*#?\d+\b",
    re.IGNORECASE,
)


def format_issues(brief: Brief) -> list[str]:
    named = [fmt for fmt, pattern in _FORMAT_WORDS.items() if re.search(pattern, brief.goal, re.IGNORECASE)]
    if named and brief.format not in named and _SAME_KIND.get(brief.format) not in named:
        issues = [
            f"The goal asks for {' / '.join(n.replace('_', ' ') for n in named)} but the format is "
            f"{brief.format.replace('_', ' ')}. Change the content type, or reword the goal."
        ]
        default = config.default_word_range(named[0])
        if default and brief.word_range and brief.word_range.max < default[0]:
            issues.append(
                f"The word range ({brief.word_range.label()}) is far below a typical "
                f"{named[0].replace('_', ' ')} ({default[0]}-{default[1]} words)."
            )
        return issues
    if _SEVERAL.search(brief.goal) and not config.sequence_length(brief.format):
        fix = ('Pick "email sequence" to write them as one job'
               if brief.format == "email" else "Run one job per piece")
        return [f"The goal asks for several pieces, but {brief.format.replace('_', ' ')} writes one. "
                f"{fix}, or ask for a single piece."]
    return []


def rule_conflicts(
    brief: Brief, profile: ClientProfile, transport: Optional[ClaudeTransport] = None,
    steps: Optional[list[str]] = None,
) -> tuple[list[str], Optional[str]]:
    """(conflicts, skipped_reason). Nothing to compare when the brief has no notes or angle hint
    and the client has no rules; an unavailable model is reported, never read as 'no conflicts'.
    `steps` are a sequence's planned emails: each is an ask too. A drip's "health, peace and play"
    email met a no-wellness-retreat rule only in review, and the email lost its topic in revision."""
    planned = "".join(f"Email {n}: {s}\n" for n, s in enumerate(steps or [], 1))
    asks = "\n".join(x for x in (brief.goal, brief.notes or "", brief.sequence_notes or "",
                                 brief.angle_hint or "", planned) if x.strip())
    if not (profile.do_not_frame or profile.do_not_say) or not asks.strip():
        return [], None

    transport = transport or ClaudeTransport()
    rules = "\n".join(f"- {r}" for r in profile.do_not_frame)
    terms = "; ".join(profile.do_not_say)
    prompt = (
        f"{_PROMPT_PATH.read_text(encoding='utf-8')}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n"
        f"Framing rules:\n{rules or '(none)'}\n"
        f"Do-not-say terms: {terms or '(none)'}\n\n"
        f"Brief goal: {brief.goal}\nAudience: {brief.audience}\n"
        f"Brief notes (must follow): {brief.notes or '(none)'}\n"
        + (f"Sequence notes (must follow across the sequence): {brief.sequence_notes}\n" if brief.sequence_notes else "")
        + f"Angle hint: {brief.angle_hint or '(none)'}"
        + (f"\nPlanned emails (each is a brief ask; check each against the rules):\n{planned}" if planned else "")
    )
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, dict):
        return [], f"rule-conflict check unavailable: {result.error or 'reply was not a JSON object'}"
    conflicts = []
    for c in parsed.get("conflicts") or []:
        if isinstance(c, dict):
            conflicts.append(
                f"Brief: \"{c.get('brief_says', '')}\" conflicts with the client rule: "
                f"\"{c.get('rule', '')}\". {c.get('resolution', '')}".strip()
            )
        elif str(c).strip():
            conflicts.append(str(c).strip())
    return conflicts, None


def check(
    brief: Brief, profile: ClientProfile, transport: Optional[ClaudeTransport] = None,
    steps: Optional[list[str]] = None,
) -> tuple[list[str], list[str]]:
    """(issues for a human, notes). Notes say what couldn't be checked."""
    conflicts, skipped = rule_conflicts(brief, profile, transport, steps=steps)
    return format_issues(brief) + conflicts, [skipped] if skipped else []
