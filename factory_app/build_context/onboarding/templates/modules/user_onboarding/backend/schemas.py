"""Schemas and helpers for the user_onboarding module.

VALID_STEPS is populated from app/config/onboarding.yaml at startup by the OSS
runtime.  The template ships a sentinel set so the validator has a safe default;
the generated app's real step IDs replace this at generation time.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Populated from app/config/onboarding.yaml step ids at generation time.
# Template sentinel — generated apps replace this with their actual step ids.
VALID_STEPS: frozenset[str] = frozenset()


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def default_steps() -> list[str]:
    """Return the list of steps from the app config (empty in the template)."""
    return list(VALID_STEPS)


def compute_progress(completed_steps: list[str]) -> float:
    """Return fractional completion (0.0–1.0); 0.0 when no steps are defined."""
    total = len(VALID_STEPS)
    if total == 0:
        return 0.0
    done = len(set(completed_steps) & VALID_STEPS)
    return round(done / total, 4)


def onboarding_state_doc(
    *,
    app_id: str | None,
    user_id: str,
) -> dict[str, Any]:
    now = timestamp_now()
    return {
        "app_id": app_id,
        "user_id": user_id,
        "completed_steps": [],
        "dismissed": False,
        "created_at": now,
        "updated_at": now,
    }


def state_response(doc: dict[str, Any]) -> dict[str, Any]:
    completed = doc.get("completed_steps") or []
    return {
        "completed_steps": completed,
        "progress": compute_progress(completed),
        "dismissed": bool(doc.get("dismissed", False)),
    }
