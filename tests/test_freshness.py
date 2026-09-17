"""Boundary tests for KB fact staleness, mirroring GTM's freshness.py test style."""
from __future__ import annotations

import datetime as dt

from pipeline.kb.freshness import MAX_VERIFY_AGE_DAYS, audit_kb, needs_reverify


def test_missing_date_needs_reverify():
    assert needs_reverify(None) is True
    assert needs_reverify("") is True


def test_unparseable_date_fails_toward_stale():
    assert needs_reverify("not-a-date") is True


def test_fresh_date_does_not_need_reverify():
    today = dt.date(2026, 9, 17)
    recent = (today - dt.timedelta(days=1)).isoformat()
    assert needs_reverify(recent, today) is False


def test_exactly_at_boundary_does_not_need_reverify():
    today = dt.date(2026, 9, 17)
    boundary = (today - dt.timedelta(days=MAX_VERIFY_AGE_DAYS)).isoformat()
    assert needs_reverify(boundary, today) is False


def test_one_day_past_boundary_needs_reverify():
    today = dt.date(2026, 9, 17)
    past_boundary = (today - dt.timedelta(days=MAX_VERIFY_AGE_DAYS + 1)).isoformat()
    assert needs_reverify(past_boundary, today) is True


def test_full_iso_timestamp_is_accepted():
    today = dt.date(2026, 9, 17)
    assert needs_reverify("2026-09-16T09:30:07Z", today) is False


class _FakeEntry:
    def __init__(self, status, verified_at):
        self.status = status
        self.verified_at = verified_at


def test_audit_kb_buckets_correctly():
    today = dt.date(2026, 9, 17)
    entries = [
        _FakeEntry("verified", (today - dt.timedelta(days=1)).isoformat()),
        _FakeEntry("verified", (today - dt.timedelta(days=MAX_VERIFY_AGE_DAYS + 1)).isoformat()),
        _FakeEntry("pending", None),
    ]
    result = audit_kb(entries, today)
    assert len(result["fresh"]) == 1
    assert len(result["stale"]) == 1
    assert len(result["never_verified"]) == 1
    assert "1 fresh" in result["summary"]
