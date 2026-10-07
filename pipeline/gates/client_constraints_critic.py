"""Judged counterpart to client_constraints.py: framing/positioning rules that can't be reduced
to a literal substring (urgency tactics, disqualified personas, category positioning) go in a
client's constraints.yaml `do_not_frame` list and are checked here by an independent LLM pass,
same discipline as voice_critic.py -- reports SKIPPED, never PASSED, if the transport is down."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.gates import judged
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "client_constraints_critic.md"


def run(
    draft: Draft, profile: ClientProfile, transport: Optional[ClaudeTransport] = None, facts: str = ""
) -> GateResult:
    if not profile.do_not_frame:
        return GateResult(gate_name="client_constraints_critic", status=GateStatus.PASSED, detail="no framing rules defined")

    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")

    rules_block = "\n".join(f"- {rule}" for rule in profile.do_not_frame)
    user_prompt = (
        f"{template}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n\n"
        f"Framing rules this draft must not violate:\n{rules_block}\n\n"
        f"{judged.shared_rules(profile, facts)}\n"
        f"{judged.SEVERITY_FORMAT}\n\n"
        f"--- DRAFT ---\n{draft.body}\n--- END DRAFT ---"
    )

    parsed, result = call_json(transport, system_prompt="", user_prompt=user_prompt)
    if not result.ok or parsed is None:
        return GateResult(
            gate_name="client_constraints_critic",
            status=GateStatus.SKIPPED,
            detail=f"judge unavailable: {result.error or 'no error detail'}",
        )

    if not isinstance(parsed, dict):
        parsed = {"verdict": None, "notes": []}
    return judged.verdict("client_constraints_critic", parsed, "clean", "clean", "judge flagged a framing violation")
