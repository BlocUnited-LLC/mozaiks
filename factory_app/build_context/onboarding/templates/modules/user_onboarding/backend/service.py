"""Business logic for the user_onboarding module."""
from __future__ import annotations

from typing import Any

from . import repo
from .schemas import (
    VALID_STEPS,
    compute_progress,
    onboarding_state_doc,
    state_response,
)


async def get_onboarding_status(ctx, *, user_id: str) -> dict[str, Any]:
    doc = await repo.get_state(ctx, user_id=user_id)
    if doc is None:
        doc = onboarding_state_doc(
            app_id=getattr(ctx, "app_id", None),
            user_id=user_id,
        )
    return state_response(doc)


async def complete_step(ctx, *, user_id: str, step_id: str) -> dict[str, Any]:
    if VALID_STEPS and step_id not in VALID_STEPS:
        raise ValueError(f"Unknown onboarding step: {step_id!r}")

    doc = await repo.get_state(ctx, user_id=user_id)
    completed = list(doc.get("completed_steps") or []) if doc else []

    if step_id not in completed:
        completed.append(step_id)
        await repo.upsert_state(ctx, user_id=user_id, update={"completed_steps": completed})

    progress = compute_progress(completed)
    await ctx.emit("hosted.onboarding.step_completed", {
        "user_id": user_id,
        "step_id": step_id,
        "completed_steps": completed,
        "progress": progress,
    })
    return {"success": True, "completed_steps": completed, "progress": progress}


async def dismiss_onboarding(ctx, *, user_id: str) -> dict[str, Any]:
    doc = await repo.get_state(ctx, user_id=user_id)
    completed = list(doc.get("completed_steps") or []) if doc else []

    await repo.upsert_state(ctx, user_id=user_id, update={"dismissed": True})
    await ctx.emit("hosted.onboarding.dismissed", {
        "user_id": user_id,
        "completed_steps": completed,
        "progress": compute_progress(completed),
    })
    return {"success": True}
