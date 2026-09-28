"""Proves a transport failure during micro-copy generation is visible as an error, not
indistinguishable from the model legitimately proposing nothing."""
from __future__ import annotations

from dataclasses import dataclass

from pipeline.llm.transport import LLMResult
from pipeline.schemas import Angle, MicrocopyField
from pipeline.stages import microcopy


def _angle() -> Angle:
    return Angle(headline="h", pitch="p", structure=[], claims_used=[])


@dataclass
class _FakeTransport:
    result: LLMResult

    def call(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return self.result


def test_successful_call_returns_candidates_and_no_error():
    transport = _FakeTransport(
        LLMResult(available=True, text='[{"text": "A title", "strategy": "benefit-led"}]')
    )
    candidates, error = microcopy.generate_field(MicrocopyField.TITLE, _angle(), transport=transport)
    assert error is None
    assert len(candidates) == 1
    assert candidates[0].text == "A title"


def test_transport_failure_returns_empty_list_with_error():
    transport = _FakeTransport(LLMResult(available=False, error="claude -p exited 1"))
    candidates, error = microcopy.generate_field(MicrocopyField.TITLE, _angle(), transport=transport)
    assert candidates == []
    assert error == "claude -p exited 1"


def test_model_returning_no_candidates_is_also_an_error():
    transport = _FakeTransport(LLMResult(available=True, text="[]"))
    candidates, error = microcopy.generate_field(MicrocopyField.CTA, _angle(), transport=transport)
    assert candidates == []
    assert error is not None


def test_generate_all_collects_errors_per_field():
    transport = _FakeTransport(LLMResult(available=False, error="claude -p exited 1"))
    candidates_by_field, errors_by_field = microcopy.generate_all(_angle(), transport=transport)
    assert set(candidates_by_field) == {f.value for f in MicrocopyField}
    assert all(c == [] for c in candidates_by_field.values())
    assert set(errors_by_field) == {f.value for f in MicrocopyField}
