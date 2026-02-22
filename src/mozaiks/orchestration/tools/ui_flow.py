"""Helpers for canonical UI tool interaction flow."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def build_ui_tool_id(
    *,
    run_id: str,
    task_id: str | None,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """Build a deterministic identifier for a UI tool invocation."""
    canonical_args = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(canonical_args.encode("utf-8")).hexdigest()[:12]
    normalized_task_id = task_id or "_"
    return f"{run_id}:{normalized_task_id}:{tool_name}:{digest}"


def is_ui_blocking(raw_value: object) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def normalize_ui_submission(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    input_value = raw.get("input")
    if isinstance(input_value, dict):
        return dict(input_value)

    arguments_value = raw.get("arguments")
    if isinstance(arguments_value, dict):
        return dict(arguments_value)

    payload_value = raw.get("payload")
    if isinstance(payload_value, dict):
        return dict(payload_value)

    return dict(raw)

