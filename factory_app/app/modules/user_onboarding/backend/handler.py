"""
Handler for the user_onboarding module.
This file is preserved across regeneration.
"""
from __future__ import annotations

from .service import UserOnboardingService


class UserOnboardingHandler:
    def __init__(self, service: UserOnboardingService | None = None) -> None:
        self._service = service or UserOnboardingService()

    async def get_onboarding_status(self, ctx, **_params) -> dict:
        return await self._service.get_onboarding_status(ctx)

    async def complete_step(self, ctx, step_id: str = "", **_params) -> dict:
        return await self._service.complete_step(ctx, step_id=step_id)

    async def dismiss_onboarding(self, ctx, **_params) -> dict:
        return await self._service.dismiss_onboarding(ctx)
