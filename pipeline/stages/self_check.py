"""Cheap same-pass reflection before spending an independent-gate cycle: does the draft cover its
outline, does every claim in the body trace to the claims list. Not a substitute for the
independent gates in pipeline/gates/ — those run regardless of what this finds."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Draft

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "self_check.md"


def check(
    draft: Draft, claims_to_use: list[str], transport: Optional[ClaudeTransport] = None
) -> dict[str, Any]:
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        f"{template}\n\nOutline:\n{draft.outline}\n\nClaims list: {claims_to_use}\n\n"
        f"--- DRAFT ---\n{draft.body}\n--- END DRAFT ---"
    )
    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, dict):
        return {
            "outline_match": None,
            "untraced_claims": [],
            "notes": [f"self-check unavailable: {result.error}"],
        }
    return parsed
