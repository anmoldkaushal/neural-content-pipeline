"""Bounded retry: each failed gate gets its own retry budget (config-driven, default 2). The
revise pass receives the SPECIFIC gate's failure reason, not a vague "try again". A third failure
on the same gate stops the job and hands the human the draft plus exactly which gate keeps
failing and why — never a silent infinite loop, never a silently lowered bar.

This module holds only the pure decision logic (should we retry, what instruction to give the
next pass); the actual loop lives in run_job.py, which is where all stages are wired together —
keeping this logic unit-testable in isolation."""
from __future__ import annotations

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


def next_revision_instruction(gate_result: GateResult) -> str:
    """Turns a gate failure into a concrete instruction for the next draft pass."""
    items = "; ".join(gate_result.flagged_items) or gate_result.detail
    return f"The previous draft failed the {gate_result.gate_name!r} gate: {items}. Fix these specific issues."
