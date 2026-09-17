"""Basic construction/validation tests for the core Pydantic models."""
from __future__ import annotations

import datetime as dt

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
)


def test_brief_minimal():
    b = Brief(client_id="exemplar", goal="write a post", audience="engineers", format="blog_post")
    assert b.client_id == "exemplar"
    assert b.target_word_count is None


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
