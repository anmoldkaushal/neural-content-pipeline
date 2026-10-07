"""Judged counterpart to entailment.py. entailment.py checks the claim IDS a draft declares; it
cannot see a fact stated in the prose without an id -- which is exactly what a writer that reads
client context (strategy figures, competitor numbers, meeting notes) could do. This gate reads the
draft cold against the verified facts and fails any statement about the client, and any specific
figure or research claim, that no verified fact supports. It replaces the old self-check stage,
whose same-purpose findings were computed and then discarded.

Same discipline as voice_critic.py: an independent call, and SKIPPED (never PASSED) when the
transport is unavailable."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.gates import judged
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import ClientProfile, Draft, GateResult, GateStatus, KBEntry

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "claims_critic.md"


def run(
    draft: Draft,
    kb_entries: list[KBEntry],
    profile: ClientProfile,
    transport: Optional[ClaudeTransport] = None,
) -> GateResult:
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    facts = "\n".join(f"- [{e.id}] {e.claim}" for e in kb_entries if e.status == "verified") or "(none)"
    user_prompt = (
        f"{template}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n\n"
        f"Verified facts:\n{facts}\n\n"
        f"{judged.shared_rules(profile)}\n"
        f"--- DRAFT ---\n{draft.body}\n--- END DRAFT ---"
    )

    parsed, result = call_json(transport, system_prompt="", user_prompt=user_prompt)
    if not result.ok or not isinstance(parsed, dict):
        return GateResult(
            gate_name="claims_critic",
            status=GateStatus.SKIPPED,
            detail=f"judge unavailable: {result.error or 'reply was not a JSON object'}",
        )

    unsupported = parsed.get("unsupported") or []
    if parsed.get("verdict") == "supported" and not unsupported:
        return GateResult(gate_name="claims_critic", status=GateStatus.PASSED, detail="every client statement is backed")

    items = []
    for u in unsupported:
        if isinstance(u, dict):
            items.append(f"\"{u.get('quote', '')}\": {u.get('why', 'no verified fact supports this')}")
        else:
            items.append(str(u))
    return GateResult(
        gate_name="claims_critic",
        status=GateStatus.FAILED,
        detail=f"{len(items) or 1} statement(s) not backed by a verified fact",
        flagged_items=items or ["judge returned an unsupported verdict without naming the statement"],
    )
