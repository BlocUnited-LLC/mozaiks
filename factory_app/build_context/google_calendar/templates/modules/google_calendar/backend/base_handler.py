"""
OSS-generated base handler for the google_calendar module.
DO NOT EDIT — regenerated on framework upgrades.
Put app-specific customizations in handler.py (the workspace subclass).
"""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import GoogleCalendarService


class GoogleCalendarBaseHandler:
    def __init__(self, service: GoogleCalendarService | None = None) -> None:
        self._service = service or GoogleCalendarService()

    async def list_upcoming_events(
        self,
        ctx: ModuleContext,
        *,
        max_results: int = 10,
        days_ahead: int = 7,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.list_upcoming_events(ctx, max_results=max_results, days_ahead=days_ahead)

    async def create_event(
        self,
        ctx: ModuleContext,
        *,
        summary: str,
        start: str,
        end: str,
        time_zone: str | None = None,
        description: str | None = None,
        location: str | None = None,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.create_event(
            ctx,
            summary=summary,
            start=start,
            end=end,
            time_zone=time_zone,
            description=description,
            location=location,
        )
