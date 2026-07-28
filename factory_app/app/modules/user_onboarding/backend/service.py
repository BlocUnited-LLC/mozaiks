"""Business logic for the user_onboarding module."""
from __future__ import annotations

from .policy import actor_id
from .repo import UserOnboardingRepo
from .schemas import VALID_STEPS, allowlist, compute_progress, default_steps, timestamp_now


class UserOnboardingService:
    def __init__(self) -> None:
        self._repo = UserOnboardingRepo()

    async def _get_or_create(self, ctx, user_id: str) -> dict:
        record = await self._repo.get(ctx, user_id=user_id)
        if record:
            return record
        now = timestamp_now()
        new_record = {
            "user_id": user_id,
            "seen_welcome": True,
            "dismissed": False,
            "steps": default_steps(),
            "completed_at": None,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.upsert(ctx, user_id=user_id, update=new_record)
        return new_record

    async def get_onboarding_status(self, ctx) -> dict:
        user_id = actor_id(ctx)
        record = await self._get_or_create(ctx, user_id)
        return allowlist(record)

    async def complete_step(self, ctx, *, step_id: str) -> dict:
        user_id = actor_id(ctx)
        if step_id not in VALID_STEPS:
            return {"success": False, "error": f"Unknown step: {step_id}"}
        record = await self._get_or_create(ctx, user_id)
        steps = record.get("steps") or default_steps()
        if steps.get(step_id, {}).get("completed"):
            return {"success": True, "steps": steps, "progress": compute_progress(steps)}
        now = timestamp_now()
        steps[step_id] = {"completed": True, "completed_at": now}
        progress = compute_progress(steps)
        completed_at = now if progress == 100 else record.get("completed_at")
        await self._repo.upsert(ctx, user_id=user_id, update={"steps": steps, "completed_at": completed_at, "updated_at": now})
        await ctx.emit("domain.onboarding.step_completed", {"user_id": user_id, "step_id": step_id, "progress": progress, "completed_at": now})
        return {"success": True, "steps": steps, "progress": progress}

    async def dismiss_onboarding(self, ctx) -> dict:
        user_id = actor_id(ctx)
        record = await self._get_or_create(ctx, user_id)
        now = timestamp_now()
        await self._repo.upsert(ctx, user_id=user_id, update={"dismissed": True, "updated_at": now})
        await ctx.emit("domain.onboarding.dismissed", {"user_id": user_id, "dismissed_at": now, "progress": compute_progress(record.get("steps") or {})})
        return {"success": True}
