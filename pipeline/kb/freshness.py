"""Fact staleness for the client knowledge base. Ported directly from
neural-pnotp-gtm/waterfall/phase_w0/freshness.py's needs_reverify() pattern: any missing or
unparseable date fails TOWARD "needs reverify," never toward trust.

That module's docstring cites its own motivating incident (a cohort sent to stale-but-"verified"
leads because mailboxes decay over time); the equivalent risk here is a fact that was true when
verified but has since gone stale (pricing, headcount, product names) being used as if still current."""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

MAX_VERIFY_AGE_DAYS = 180  # facts older than this need re-confirmation before use


def needs_reverify(verified_at: Optional[str], today: Optional[dt.date] = None) -> bool:
    if not verified_at:
        return True
    today = today or dt.date.today()
    date_part = verified_at.split("T")[0]
    try:
        d = dt.date.fromisoformat(date_part)
    except ValueError:
        return True
    return (today - d).days > MAX_VERIFY_AGE_DAYS


def audit_kb(entries: list[Any], today: Optional[dt.date] = None) -> dict[str, Any]:
    """entries: objects with .status and .verified_at (KBEntry or compatible). Buckets into
    fresh/stale/never_verified, mirroring freshness.py's audit_ledger()."""
    today = today or dt.date.today()
    fresh: list[Any] = []
    stale: list[Any] = []
    never_verified: list[Any] = []

    for e in entries:
        if e.status != "verified":
            never_verified.append(e)
            continue
        verified_at = e.verified_at.isoformat() if hasattr(e.verified_at, "isoformat") else e.verified_at
        if needs_reverify(verified_at, today):
            stale.append(e)
        else:
            fresh.append(e)

    summary = (
        f"{len(fresh)} fresh, {len(stale)} stale, {len(never_verified)} never verified "
        f"(of {len(entries)} total)"
    )
    return {"fresh": fresh, "stale": stale, "never_verified": never_verified, "summary": summary}
