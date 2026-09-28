"""Proves client_constraints_critic judges do_not_frame rules independently of the literal
do_not_say gate: a draft can pass the substring check and still get caught here, and the gate
degrades to SKIPPED (never PASSED) when the transport is unavailable."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pipeline.gates import client_constraints_critic
from pipeline.llm.transport import LLMResult
from pipeline.schemas import ClientProfile, Draft, GateStatus


def _draft(body: str) -> Draft:
    return Draft(job_id="t1", body=body, word_count=len(body.split()), outline=[], claims_used=[])


def _profile(**kwargs) -> ClientProfile:
    return ClientProfile(client_id="t", company_name="T", industry="x", **kwargs)


@dataclass
class _FakeTransport:
    result: LLMResult

    def call(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return self.result


def test_no_rules_defined_passes_without_calling_transport():
    profile = _profile(do_not_frame=[])
    result = client_constraints_critic.run(_draft("anything at all"), profile, transport=None)
    assert result.status == GateStatus.PASSED


def test_clean_verdict_passes():
    profile = _profile(do_not_frame=["Do not use urgency or scarcity tactics."])
    transport = _FakeTransport(LLMResult(available=True, text='{"verdict": "clean", "notes": []}'))
    result = client_constraints_critic.run(_draft("A calm, unhurried invitation."), profile, transport=transport)
    assert result.status == GateStatus.PASSED


def test_violation_verdict_fails_with_notes():
    profile = _profile(do_not_frame=["Do not use urgency or scarcity tactics."])
    transport = _FakeTransport(
        LLMResult(available=True, text='{"verdict": "violation", "notes": ["only 3 spots left is scarcity pressure"]}')
    )
    result = client_constraints_critic.run(_draft("Only 3 spots left, apply today!"), profile, transport=transport)
    assert result.status == GateStatus.FAILED
    assert any("scarcity" in item for item in result.flagged_items)


def test_transport_unavailable_is_skipped_not_passed():
    profile = _profile(do_not_frame=["Do not use urgency or scarcity tactics."])
    transport = _FakeTransport(LLMResult(available=False, error="claude -p exited 1"))
    result = client_constraints_critic.run(_draft("Some copy."), profile, transport=transport)
    assert result.status == GateStatus.SKIPPED
    assert "unavailable" in result.detail
