"""Judged counterpart to entailment.py. entailment.py checks the claim IDS a draft declares; it
cannot see a fact stated in the prose without an id -- which is exactly what a writer that reads
client context (strategy figures, competitor numbers, meeting notes) could do. This gate reads the
draft cold against the verified facts and fails any statement about the client, and any specific
figure or research claim, that no verified fact supports. Describing the reader's own situation in
general terms is not a claim: the judge is shown who the reader is, so naming their problem doesn't
fail as an unbacked fact. Each finding is blocking or minor, as in the other judges (judged.py), and
only blocking findings fail; a finding with no severity counts as blocking. It replaces the old self-check stage,
whose same-purpose findings were computed and then discarded.

Public facts (pipeline/stages/research.py) support statements about the world, never about the
client. A blocking statement the judge marks as about the world is passed back in
world_statements, so the job can try to source it before a revision cuts it.

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
    theme_words: Optional[list[str]] = None,
    reader: str = "",
) -> GateResult:
    """`theme_words`: words the client's plan has the writer use (a sequence email's anchor word).
    Used as an ordinary word, one isn't a claim; presented as a named feature of the offering, it is.
    `reader`: the brief's audience and reader profile, so the judge can tell a description of the
    reader's situation from a claim. Targeting context, never a fact."""
    transport = transport or ClaudeTransport()
    themes = ""
    if theme_words:
        themes = ("Theme words the client's plan asks the writer to use: " + ", ".join(theme_words) + ". Used as an "
                  "ordinary word, a theme word is not a statement about the client and needs no support. Presented "
                  "as a named feature, programme or part of the offering (\"the Reset\", \"our Headroom sessions\"), "
                  "it is a statement about the client like any other.\n\n")
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    verified = [e for e in kb_entries if e.status == "verified"]
    facts = "\n".join(f"- [{e.id}] {e.claim}" for e in verified if e.kind != "public") or "(none)"
    public = "\n".join(f"- [{e.id}] {e.claim} ({e.source_doc})" for e in verified if e.kind == "public")
    user_prompt = (
        f"{template}\n\n"
        f"Client: {profile.company_name} ({profile.industry})\n\n"
        f"Verified facts:\n{facts}\n\n"
        + (f"Public facts (checked against public sources; they support statements about the world, never "
           f"about the client):\n{public}\n\n" if public else "")
        + f"{judged.shared_rules(profile)}\n"
        f"{themes}"
        + (f"READER (who the piece is for; context, not facts):\n{reader.strip()}\n\n" if reader.strip() else "")
        + f"--- DRAFT ---\n{draft.body}\n--- END DRAFT ---"
    )

    parsed, result = call_json(transport, system_prompt="", user_prompt=user_prompt)
    if not result.ok or not isinstance(parsed, dict):
        return GateResult(
            gate_name="claims_critic",
            status=GateStatus.SKIPPED,
            detail=f"judge unavailable: {result.error or 'reply was not a JSON object'}",
        )

    blocking, minor, world = [], [], []
    for u in parsed.get("unsupported") or []:
        if isinstance(u, dict):
            text = f"\"{u.get('quote', '')}\": {u.get('why', 'no verified fact supports this')}"
            is_minor = str(u.get("severity", "")).lower() == "minor"
            (minor if is_minor else blocking).append(text)
            if not is_minor and str(u.get("about", "")).lower() == "world" and str(u.get("quote", "")).strip():
                world.append(str(u["quote"]).strip())
        else:
            blocking.append(str(u))  # fails closed: no severity counts as blocking
    if blocking or (parsed.get("verdict") != "supported" and not minor):
        return GateResult(
            gate_name="claims_critic",
            status=GateStatus.FAILED,
            detail=f"{len(blocking) or 1} statement(s) not backed by a verified fact",
            flagged_items=blocking or ["judge returned an unsupported verdict without naming the statement"],
            notes=minor,
            world_statements=world,
        )
    detail = "every client statement is backed" + (f", {len(minor)} minor note(s) for review" if minor else "")
    return GateResult(gate_name="claims_critic", status=GateStatus.PASSED, detail=detail, notes=minor)
