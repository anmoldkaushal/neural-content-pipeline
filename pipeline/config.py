"""Loads config/pipeline_config.yaml. Every consumer falls back to the same default it would use
if this file didn't exist — the file exists to make the defaults visible and tunable in one
place, not to be load-bearing on its own. (Not part of the original file-tree plan; added because
a "tunable knob" file that nothing actually reads is decorative, not a config file.)"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pipeline_config.yaml"

_cache: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    global _cache
    if _cache is None:
        if _CONFIG_PATH.exists():
            _cache = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        else:
            _cache = {}
    return _cache


def retry_budget(fallback: int = 2) -> int:
    return int(load().get("retry_budget_per_gate", fallback))


def microcopy_cap(field_name: str, fallback: int) -> int:
    return int(load().get("microcopy_caps", {}).get(field_name, fallback))


def transport_default() -> str:
    return str(load().get("transport", {}).get("default", "cli"))


def formats() -> dict[str, dict[str, Any]]:
    return dict(load().get("formats") or {})


def microcopy_fields(format_: str | None, fallback: dict[str, int]) -> dict[str, int]:
    """Field -> candidate count for a content type. Unknown or missing formats get the generic
    title/subtitle/hook/cta set, each capped by microcopy_caps."""
    fmt = formats().get(format_ or "", {})
    if "microcopy" in fmt:  # an explicit empty mapping means the format has no micro-copy
        return {str(k): int(v) for k, v in (fmt["microcopy"] or {}).items()}
    return {name: microcopy_cap(name, count) for name, count in fallback.items()}


def sequence_length(format_: str | None) -> int | None:
    """How many pieces a sequence format writes (email_sequence: 5), or None for one piece."""
    value = formats().get(format_ or "", {}).get("sequence")
    return int(value) if value else None


def default_word_range(format_: str) -> tuple[int, int] | None:
    value = formats().get(format_, {}).get("word_range")
    if not value or len(value) != 2:
        return None
    return int(value[0]), int(value[1])


def email_copy_fields(format_: str | None) -> dict[str, int]:
    """Field -> alternatives for a sequence email's own copy (subject line, preheader)."""
    return {str(k): int(v) for k, v in (formats().get(format_ or "", {}).get("email_copy") or {}).items()}


def sequence_lint() -> dict[str, int]:
    """Shared-run lengths (in words) at which sequence_lint calls an email a repeat of an earlier one."""
    knobs = {"opening_ngram": 4, "cta_ngram": 5, "phrase_ngram": 7}
    knobs.update({k: int(v) for k, v in (load().get("sequence_lint") or {}).items()})
    return knobs


def word_range_tolerance(fallback: float = 0.1) -> float:
    return float(load().get("word_range_tolerance", fallback))


def context_budget_chars(fallback: int = 14000) -> int:
    return int(load().get("context_budget_chars", fallback))
