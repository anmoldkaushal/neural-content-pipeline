"""Catches a brief that can't pass before anything is drafted. Two checks:

- format fit (deterministic): the goal names a content type the brief's format isn't ("write the
  first two blog posts" sent as an 80-word social_post);
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


def format_issues(brief: Brief) -> list[str]:
    named = [fmt for fmt, pattern in _FORMAT_WORDS.items() if re.search(pattern, brief.goal, re.IGNORECASE)]
    if not named or brief.format in named:
        return []
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


def rule_conflicts(
    brief: Brief, profile: ClientProfile, transport: Optional[ClaudeTransport] = None
) -> tuple[list[str], Optional[str]]:
    """(conflicts, skipped_reason). Nothing to compare when the brief has no notes or angle hint
    and the client has no rules; an unavailable model is reported, never read as 'no conflicts'."""
    asks = "\n".join(x for x in (brief.goal, brief.notes or "", brief.angle_hint or "") if x.strip())
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
        f"Angle hint: {brief.angle_hint or '(none)'}"
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
    brief: Brief, profile: ClientProfile, transport: Optional[ClaudeTransport] = None
) -> tuple[list[str], list[str]]:
    """(issues for a human, notes). Notes say what couldn't be checked."""
    conflicts, skipped = rule_conflicts(brief, profile, transport)
    return format_issues(brief) + conflicts, [skipped] if skipped else []
