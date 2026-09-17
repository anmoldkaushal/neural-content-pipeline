"""Main body generation — the one expensive, single-commit pass. Everything upstream (angle, tone,
brief synthesis) exists to make sure this pass only runs once per accepted direction."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pipeline.llm.transport import ClaudeTransport, call_json
from pipeline.schemas import Draft

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "draft.md"


def write_draft(
    job_id: str,
    working_spec: dict[str, Any],
    transport: Optional[ClaudeTransport] = None,
    revision: int = 0,
) -> Draft:
    transport = transport or ClaudeTransport()
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = f"{template}\n\nWorking spec:\n{working_spec}"

    parsed, result = call_json(transport, system_prompt="", user_prompt=prompt)
    if not result.ok or not isinstance(parsed, dict):
        return Draft(
            job_id=job_id,
            body="",
            word_count=0,
            outline=working_spec.get("outline", []),
            claims_used=[],
            revision=revision,
        )

    body = str(parsed.get("body", ""))
    return Draft(
        job_id=job_id,
        body=body,
        word_count=len(body.split()),
        outline=working_spec.get("outline", []),
        claims_used=list(parsed.get("claims_used", [])),
        revision=revision,
    )
