"""Body generation: one full drafting pass, then revision passes that EDIT that draft.

Everything the gates will check is in front of the writer before it writes: the client's rules
(pipeline/kb/rulebook.py), the verified facts with their text (not just their ids), the relevant
client context, and approved or past examples for voice. A revision gets the previous draft and
every gate finding (pipeline/stages/revise.py) and returns the same piece with those fixes, rather
than regenerating from the spec and losing what already passed."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pipeline.gates.style_lint import count_words
from pipeline.kb.approved_examples import format_example_block
from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Draft, KBEntry

_PROMPTS = Path(__file__).resolve().parent.parent / "llm" / "prompts"
_PROMPT_PATH = _PROMPTS / "draft.md"
_REVISE_PROMPT_PATH = _PROMPTS / "revise.md"

# Prompt context, not spec: rendered as their own blocks rather than dumped with the spec dict.
_NOT_SPEC = {"approved_examples"}


def facts_block(kb_entries: list[KBEntry], planned_ids: list[str]) -> str:
    """Verified facts with their text, the angle's planned ones first. The only statements about
    the client a draft may make."""
    verified = [e for e in kb_entries if e.status == "verified"]
    planned = [e for e in verified if e.id in set(planned_ids)]
    others = [e for e in verified if e.id not in set(planned_ids)]
    lines = ["VERIFIED FACTS (the only statements about the client you may make; cite ids in claims_used):"]
    lines += [f"- [{e.id}] {e.claim}" for e in planned] or ["- (none planned)"]
    if others:
        lines.append("Also verified, use only if they genuinely help:")
        lines += [f"- [{e.id}] {e.claim}" for e in others]
    return "\n".join(lines) + "\n"


def _spec_for_prompt(working_spec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in working_spec.items() if k not in _NOT_SPEC}


def _voice_block(working_spec: dict[str, Any], voice_reference: str) -> str:
    examples = working_spec.get("approved_examples") or []
    return format_example_block(examples) if examples else voice_reference


def _draft_from(job_id: str, parsed: Any, working_spec: dict[str, Any], revision: int, fallback: Optional[Draft] = None) -> Draft:
    if not isinstance(parsed, dict) or not str(parsed.get("body", "")).strip():
        if fallback is not None:
            # A failed revision call keeps the last real draft rather than replacing it with nothing.
            return fallback.model_copy(update={"revision": revision, "change_notes": ["revision call failed; previous draft kept"]})
        return Draft(job_id=job_id, body="", word_count=0, outline=working_spec.get("outline", []),
                     claims_used=[], revision=revision)
    body = str(parsed.get("body", ""))
    return Draft(
        job_id=job_id,
        body=body,
        word_count=count_words(body),
        outline=working_spec.get("outline", []),
        claims_used=[str(c) for c in parsed.get("claims_used", [])],
        revision=revision,
        change_notes=[str(n) for n in parsed.get("change_notes", []) or []],
        # A revision that leaves the copy out keeps the previous email's copy.
        subject_line=str(parsed.get("subject_line") or "").strip() or (fallback.subject_line if fallback else None),
        preheader=str(parsed.get("preheader") or "").strip() or (fallback.preheader if fallback else None),
    )


def write_draft(
    job_id: str,
    working_spec: dict[str, Any],
    transport: Optional[ClaudeTransport] = None,
    revision: int = 0,
    rules: str = "",
    facts: str = "",
    context_block: str = "",
    voice_reference: str = "",
) -> Draft:
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        f"{template}\n{rules}\n{facts}\n{context_block}\n{_voice_block(working_spec, voice_reference)}\n"
        f"Working spec:\n{_spec_for_prompt(working_spec)}"
    )
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    return _draft_from(job_id, parsed if result.ok else None, working_spec, revision)


def revise_draft(
    job_id: str,
    working_spec: dict[str, Any],
    previous: Draft,
    feedback: str,
    transport: Optional[ClaudeTransport] = None,
    rules: str = "",
    facts: str = "",
    context_block: str = "",
) -> Draft:
    """Edits `previous` to address `feedback`. Falls back to a fresh draft when there is nothing to
    edit (the previous call returned no body)."""
    revision = previous.revision + 1
    if not previous.body.strip():
        return write_draft(job_id, working_spec, transport, revision, rules, facts, context_block)

    transport = transport or ClaudeTransport()
    template = _REVISE_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        f"{template}\n{rules}\n{facts}\n{context_block}\n"
        f"Working spec:\n{_spec_for_prompt(working_spec)}\n\n"
        f"WHAT TO FIX:\n{feedback}\n\n"
        f"Claims the previous draft cited: {previous.claims_used}\n"
        + (f"Subject line: {previous.subject_line}\nPreheader: {previous.preheader}\n" if previous.subject_line else "")
        + f"--- PREVIOUS DRAFT ({previous.word_count} words) ---\n{previous.body}\n--- END PREVIOUS DRAFT ---"
    )
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    return _draft_from(job_id, parsed if result.ok else None, working_spec, revision, fallback=previous)
