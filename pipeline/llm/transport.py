"""LLM call transport: shells out to the Claude Code CLI by default (house rule — pipeline code
does not consume metered API credits unless explicitly told to), with a direct SDK path as an
opt-in override. Ported from longevity-science-daily/web/scripts/agents/depth/llm_judge.py's
env-var contract, renamed for this repo (CONTENT_JUDGE_* instead of LSD_JUDGE_*).

Transport is never allowed to silently fake success: if neither the CLI nor the SDK is usable,
callers get an unavailable result and MUST treat it as a skipped call, never a pass.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Union

TRANSPORT_ENV = "CONTENT_JUDGE_TRANSPORT"
CLAUDE_BIN_ENV = "CONTENT_CLAUDE_BIN"
CLI_MODEL_ENV = "CONTENT_JUDGE_CLI_MODEL"
TIMEOUT_ENV = "CONTENT_JUDGE_TIMEOUT"

DEFAULT_CLI_MODEL = "sonnet"
DEFAULT_SDK_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_TIMEOUT_SECONDS = 180


@dataclass
class LLMResult:
    available: bool
    text: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.available and self.text is not None


class ClaudeTransport:
    """Resolves transport once at construction, same pattern as LLMJudge.__init__ in llm_judge.py."""

    def __init__(self) -> None:
        requested = (os.getenv(TRANSPORT_ENV) or "cli").strip().lower()
        self.timeout = int(os.getenv(TIMEOUT_ENV, str(DEFAULT_TIMEOUT_SECONDS)))
        self._cli_path: Optional[str] = None
        self._sdk_client = None
        self.model = os.getenv(CLI_MODEL_ENV) or DEFAULT_CLI_MODEL

        if requested == "off":
            self.transport = "offline"
            return

        if requested == "sdk":
            self.transport = self._init_sdk()
            return

        # default: cli
        self._cli_path = shutil.which(os.getenv(CLAUDE_BIN_ENV) or "claude")
        self.transport = "cli" if self._cli_path else "offline"

    def _init_sdk(self) -> str:
        try:
            import anthropic  # type: ignore
        except ImportError:
            return "offline"
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "offline"
        self._sdk_client = anthropic.Anthropic(api_key=api_key)
        self.model = DEFAULT_SDK_MODEL
        return "sdk"

    def call(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if self.transport == "offline":
            return LLMResult(available=False, error="no CLI or SDK transport available")
        if self.transport == "cli":
            return self._call_cli(system_prompt, user_prompt)
        return self._call_sdk(system_prompt, user_prompt)

    def _call_cli(self, system_prompt: str, user_prompt: str) -> LLMResult:
        assert self._cli_path is not None
        combined = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        # The prompt goes over stdin, not argv: with client context and a previous draft in it,
        # a prompt can outgrow what a single command-line argument comfortably carries.
        cmd = [self._cli_path, "-p", "--model", self.model]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout, input=combined
            )
        except subprocess.TimeoutExpired:
            return LLMResult(available=False, error=f"claude -p timed out after {self.timeout}s")
        except OSError as exc:
            return LLMResult(available=False, error=f"failed to invoke claude CLI: {exc}")

        if proc.returncode != 0:
            return LLMResult(
                available=False,
                error=f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}",
            )
        return LLMResult(available=True, text=proc.stdout.strip())

    def _call_sdk(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if self._sdk_client is None:
            return LLMResult(available=False, error="sdk transport requested but not initialized")
        try:
            response = self._sdk_client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt or "",
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:  # noqa: BLE001 - surface any SDK error as an unavailable result
            return LLMResult(available=False, error=str(exc))
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        return LLMResult(available=True, text=text.strip())


def call_json(
    transport: ClaudeTransport, system_prompt: str, user_prompt: str
) -> tuple[Optional[Union[dict, list]], LLMResult]:
    """Convenience wrapper for stages that expect a single JSON object/array back."""
    result = transport.call(
        system_prompt,
        user_prompt + "\n\nRespond with ONLY a single raw JSON value, no prose, no markdown fences.",
    )
    if not result.ok:
        return None, result

    text = (result.text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text), result
    except json.JSONDecodeError as exc:
        # A valid value followed by stray text (a trailing note, a second fence) is still the answer.
        try:
            value, _ = json.JSONDecoder().raw_decode(text)
            return value, result
        except json.JSONDecodeError:
            return None, LLMResult(available=False, error=f"model did not return valid JSON: {exc}")
