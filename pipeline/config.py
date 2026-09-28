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
    spec = formats().get(format_ or "", {}).get("microcopy")
    if spec:
        return {str(k): int(v) for k, v in spec.items()}
    return {name: microcopy_cap(name, count) for name, count in fallback.items()}


def default_word_count(format_: str) -> int | None:
    value = formats().get(format_, {}).get("target_word_count")
    return int(value) if value is not None else None
