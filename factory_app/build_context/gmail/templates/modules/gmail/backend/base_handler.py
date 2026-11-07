"""
OSS-generated base handler for the gmail module.
DO NOT EDIT — regenerated on framework upgrades.
Put app-specific customizations in handler.py (the workspace subclass).
"""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import GmailService


class GmailBaseHandler:
    def __init__(self, service: GmailService | None = None) -> None:
        self._service = service or GmailService()

    async def send_email(
        self,
        ctx: ModuleContext,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.send_email(ctx, to=to, subject=subject, body=body, cc=cc)
