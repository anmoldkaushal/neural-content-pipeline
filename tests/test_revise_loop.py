"""Proves the retry loop is bounded and escalates on the Nth failure, not silent and not infinite."""
from __future__ import annotations

from pipeline.schemas import GateResult, GateStatus
from pipeline.stages.revise import next_revision_instruction, should_retry


def _failing_result(gate_name="style_lint", items=None):
    return GateResult(
        gate_name=gate_name,
        status=GateStatus.FAILED,
        detail="1 violation",
        flagged_items=items or ["banned word: 'leverage'"],
    )


def _passing_result(gate_name="style_lint"):
    return GateResult(gate_name=gate_name, status=GateStatus.PASSED, detail="clean")


def test_retries_while_under_budget():
    result = _failing_result()
    assert should_retry(result, attempts_so_far=0, budget=2) is True
    assert should_retry(result, attempts_so_far=1, budget=2) is True


def test_stops_retrying_at_budget():
    result = _failing_result()
    assert should_retry(result, attempts_so_far=2, budget=2) is False
    assert should_retry(result, attempts_so_far=5, budget=2) is False


def test_passing_gate_never_retries():
    result = _passing_result()
    assert should_retry(result, attempts_so_far=0, budget=2) is False


def test_revision_instruction_names_the_specific_failure():
    result = _failing_result(items=["banned word: 'leverage'", "too short: 40 words (min 50)"])
    instruction = next_revision_instruction(result)
    assert "style_lint" in instruction
    assert "leverage" in instruction
    assert "too short" in instruction


def test_loop_is_bounded_end_to_end():
    """Simulates a gate that always fails; the driving loop (like run_job.py's) must stop at the
    budget rather than looping forever."""
    budget = 2
    attempts = 0
    result = _failing_result()
    iterations = 0
    while should_retry(result, attempts, budget):
        attempts += 1
        iterations += 1
        if iterations > 100:  # safety net for the TEST itself, not the code under test
            raise AssertionError("loop did not terminate")
    assert attempts == budget
