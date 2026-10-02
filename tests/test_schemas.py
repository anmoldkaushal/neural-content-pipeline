"""Basic construction/validation tests for the core Pydantic models."""
from __future__ import annotations

import datetime as dt

import pytest

from pipeline.schemas import (
    Angle,
    Brief,
    ClientProfile,
    Draft,
    GateResult,
    GateStatus,
    JobRecord,
    KBEntry,
    MicrocopyCandidate,
    MicrocopyField,
    TonePreset,
    WordRange,
)


def test_brief_minimal():
    b = Brief(client_id="exemplar", goal="write a post", audience="engineers", format="blog_post")
    assert b.client_id == "exemplar"
    assert b.word_range is None
    assert b.plan_item_id is None


def test_brief_reads_a_legacy_target_as_a_range():
    b = Brief(client_id="c", goal="g", audience="a", format="email", target_word_count=100)
    assert (b.word_range.min, b.word_range.max) == (85, 115)
    explicit = Brief(client_id="c", goal="g", audience="a", format="email",
                     word_range={"min": 40, "max": 60}, target_word_count=500)
    assert (explicit.word_range.min, explicit.word_range.max) == (40, 60)


def test_word_range_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        WordRange(min=300, max=200)
    assert WordRange(min=100, max=200).label() == "100-200 words"


def test_kb_entry_defaults_to_pending():
    e = KBEntry(id="kb-1", claim="a fact", source_doc="doc.txt")
    assert e.status == "pending"
    assert e.verified_at is None


def test_client_profile_extension_lists_default_empty():
    p = ClientProfile(client_id="acme", company_name="Acme", industry="widgets")
    assert p.banned_words == []
    assert p.tone_presets == []


def test_tone_preset_roundtrip():
    t = TonePreset(name="bold", description="confident", sample_line="We ship on time.")
    assert t.name == "bold"


def test_angle_requires_claims_used_list():
    a = Angle(headline="h", pitch="p", structure=["intro"], claims_used=["kb-1"])
    assert a.claims_used == ["kb-1"]


def test_microcopy_candidate_field_enum():
    c = MicrocopyCandidate(field=MicrocopyField.CTA, text="Start now", strategy="urgency-led")
    assert c.field == MicrocopyField.CTA


def test_draft_word_count_field():
    d = Draft(job_id="j1", body="one two three", word_count=3, outline=["intro"], claims_used=[])
    assert d.word_count == 3


def test_gate_result_status_values():
    g = GateResult(gate_name="style_lint", status=GateStatus.PASSED, detail="clean")
    assert g.status == GateStatus.PASSED


def test_job_record_defaults():
    r = JobRecord(job_id="j1", client_id="exemplar", brief_summary="a brief")
    assert r.status == "in_progress"
    assert r.stages_run == []
    assert isinstance(r.created_at, dt.datetime)
