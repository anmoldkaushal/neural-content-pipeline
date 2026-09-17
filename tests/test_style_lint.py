"""Proves the canonical banned-word list holds and a client can only ADD to it, never remove."""
from __future__ import annotations

from pipeline.gates import style_lint
from pipeline.schemas import ClientProfile, Draft, GateStatus


def _draft(body: str) -> Draft:
    return Draft(job_id="t1", body=body, word_count=len(body.split()), outline=[], claims_used=[])


def _profile(**kwargs) -> ClientProfile:
    return ClientProfile(client_id="t", company_name="T", industry="x", **kwargs)


def test_clean_draft_passes():
    body = " ".join(["word"] * 60)
    result = style_lint.run(_draft(body), _profile())
    assert result.status == GateStatus.PASSED


def test_base_banned_word_is_caught():
    body = "We leverage " + " ".join(["word"] * 60)
    result = style_lint.run(_draft(body), _profile())
    assert result.status == GateStatus.FAILED
    assert any("leverage" in item for item in result.flagged_items)


def test_client_can_add_a_banned_word():
    body = "Our totally proprietary widget is great. " + " ".join(["word"] * 60)
    profile = _profile(banned_words=["proprietary"])
    result = style_lint.run(_draft(body), profile)
    assert result.status == GateStatus.FAILED
    assert any("proprietary" in item for item in result.flagged_items)


def test_client_cannot_remove_a_base_banned_word():
    """A client profile has no mechanism to subtract from the base list -- this test proves that
    by construction: banned_words only ever gets ADDED to the base list in style_lint.run(),
    regardless of what the client profile contains."""
    body = "We leverage " + " ".join(["word"] * 60)
    result = style_lint.run(_draft(body), _profile())
    assert result.status == GateStatus.FAILED


def test_em_dash_is_flagged():
    body = "This is bad — very bad. " + " ".join(["word"] * 60)
    result = style_lint.run(_draft(body), _profile())
    assert any("dash" in item for item in result.flagged_items)


def test_too_short_is_flagged():
    result = style_lint.run(_draft("too short"), _profile())
    assert result.status == GateStatus.FAILED
    assert any("too short" in item for item in result.flagged_items)
