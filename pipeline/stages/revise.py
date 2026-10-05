"""Bounded retry: each failed gate gets its own retry budget (config-driven, default 2). The
revise pass receives the SPECIFIC gate's failure reason, not a vague "try again". A third failure
on the same gate stops the job and hands the human the draft plus exactly which gate keeps
failing and why — never a silent infinite loop, never a silently lowered bar.

This module holds only the pure decision logic (should we retry, what instruction to give the
next pass); the actual loop lives in run_job.py, which is where all stages are wired together —
keeping this logic unit-testable in isolation."""
from __future__ import annotations

from typing import Union

from pipeline.schemas import GateResult, GateStatus


class RetryBudgetExceeded(Exception):
    def __init__(self, gate_name: str, attempts: int, last_result: GateResult) -> None:
        self.gate_name = gate_name
        self.attempts = attempts
        self.last_result = last_result
        super().__init__(
            f"gate {gate_name!r} still failing after {attempts} attempt(s): {last_result.detail}"
        )


def should_retry(gate_result: GateResult, attempts_so_far: int, budget: int) -> bool:
    return gate_result.status == GateStatus.FAILED and attempts_so_far < budget


def next_revision_instruction(gate_results: Union[GateResult, list[GateResult]]) -> str:
    """Turns gate failures into one concrete instruction for the next draft pass. Every failing
    gate goes in at once: fixing one gate's notes alone tends to break another."""
    if isinstance(gate_results, GateResult):
        gate_results = [gate_results]
    parts = [f"the {g.gate_name!r} gate: {'; '.join(g.flagged_items) or g.detail}" for g in gate_results]
    return f"The previous draft failed {' AND '.join(parts)}. Fix all of these specific issues."
