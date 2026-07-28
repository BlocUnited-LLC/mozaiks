"""Constants and typed shapes for the user_onboarding module."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Must match step_id enum in module.yaml
VALID_STEPS = {"create_app", "explore_apps", "open_support"}
TOTAL_STEPS = len(VALID_STEPS)

Document = dict[str, Any]


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def default_steps() -> dict[str, dict]:
    return {step: {"completed": False, "completed_at": None} for step in sorted(VALID_STEPS)}


def compute_progress(steps: dict) -> int:
    if not steps:
        return 0
    done = sum(1 for s in steps.values() if s.get("completed"))
    return round((done / TOTAL_STEPS) * 100)


def allowlist(record: Document) -> Document:
    return {
        "seen_welcome": record.get("seen_welcome", True),
        "dismissed": record.get("dismissed", False),
        "steps": record.get("steps") or default_steps(),
        "progress": compute_progress(record.get("steps") or {}),
        "completed_at": record.get("completed_at"),
    }
