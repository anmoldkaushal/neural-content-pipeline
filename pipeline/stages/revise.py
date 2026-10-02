"""Bounded retry: each failed gate gets its own retry budget (config-driven, default 2). The
revise pass receives every failing gate's specific findings in one round, plus what earlier rounds
were told to fix -- not a vague "try again", and not one gate at a time. Fixing gates one per round
spent the budget on the order gates happened to run in, and a revision told only about the latest
failure could quietly reintroduce an earlier one. When any gate exhausts its budget the job stops
and hands the human the draft plus exactly which gates keep failing and why -- never a silent
infinite loop, never a silently lowered bar.

This module holds only the pure decision logic (should we retry, what to tell the next pass); the
loop lives in run_job.py, keeping this unit-testable in isolation."""
from __future__ import annotations

from pipeline.schemas import GateResult, GateStatus

# What each gate checks, in words the writer can act on.
_GATE_MEANING = {
    "style_lint": "house style: banned words and phrases, dashes, and length",
    "client_constraints": "the client's do-not-say terms (exact match)",
    "client_constraints_critic": "the client's framing rules, judged on meaning",
    "entailment": "claim ids must be verified knowledge-base facts",
    "claims_critic": "every statement about the client must be backed by a verified fact",
    "voice_critic": "voice and tone fit, and not reading as generic AI copy",
}


class RetryBudgetExceeded(Exception):
    def __init__(self, gate_name: str, attempts: int, last_result: GateResult, also_failing: list[str] | None = None) -> None:
        self.gate_name = gate_name
        self.attempts = attempts
        self.last_result = last_result
        self.also_failing = also_failing or []
        others = f" (also failing: {', '.join(self.also_failing)})" if self.also_failing else ""
        super().__init__(
            f"gate {gate_name!r} still failing after {attempts} attempt(s): {last_result.detail}{others}"
        )


def should_retry(gate_result: GateResult, attempts_so_far: int, budget: int) -> bool:
    return gate_result.status == GateStatus.FAILED and attempts_so_far < budget


def _items(result: GateResult) -> list[str]:
    return result.flagged_items or [result.detail]


def next_revision_instruction(failed: GateResult | list[GateResult], history: list[list[GateResult]] | None = None) -> str:
    """Turns this round's gate failures (and earlier rounds') into the next pass's instructions."""
    failed = [failed] if isinstance(failed, GateResult) else failed
    lines = ["The previous draft failed these checks. Fix every item below:"]
    for result in failed:
        meaning = _GATE_MEANING.get(result.gate_name, "")
        lines.append(f"\n[{result.gate_name}]" + (f" ({meaning})" if meaning else ""))
        lines += [f"- {item}" for item in _items(result)]

    earlier = [r for round_ in (history or []) for r in round_]
    if earlier:
        lines.append("\nEarlier rounds were told to fix these; do not reintroduce any of them:")
        lines += [f"- [{r.gate_name}] {item}" for r in earlier for item in _items(r)]
    return "\n".join(lines)
