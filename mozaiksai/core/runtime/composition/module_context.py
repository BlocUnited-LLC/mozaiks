"""ModuleContext — runtime context injected into every module action handler.

Module handlers never reach into the request cycle themselves. The runtime
builds this context from the incoming request and injects it so that:
  - Handlers are testable with a mock context
  - Auth/tenant info is never pulled from globals
  - Event emission goes through one consistent path
  - Generated module repos can use ctx.persistence for app-scoped storage
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from mozaiksai.core.runtime.persistence.adapter import ModulePersistenceContext


@dataclass
class ModuleContext:
    """Context injected into every module action call.

    Usage in a module handler:
        async def list(self, ctx: ModuleContext, *, limit: int = 20):
            results = await some_db.find(app_id=ctx.app_id)
            await ctx.emit("contacts.listed", {"count": len(results)})
            return results
    """

    # Tenant identity
    app_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None

    # Request tracing
    correlation_id: str | None = None

    # Auth token forwarded from the incoming request (for external API calls)
    auth_token: str | None = None

    # Resolved permission ids for this caller/action dispatch.  None preserves
    # trusted internal runtime calls; external dispatch normally supplies a list.
    permissions: list[str] | None = None

    # Setting definitions declared in settings.yaml for this module.
    # Each entry is a setting definition dict: {id, type, default, label, ...}.
    # Handlers use this to resolve defaults or validate setting-aware logic.
    settings: list[dict[str, Any]] | None = None

    # App-scoped generated-module persistence. ModuleExecutor injects this
    # when app_id is available. It may be None for explicitly constructed test
    # contexts or trusted runtime calls. ctx.db is intentionally not provided.
    persistence: ModulePersistenceContext | None = None

    # Event emitter — async callable(event_type, payload) -> None
    # Injected by ModuleExecutor; no-op if not wired.
    _emit: Callable[[str, dict[str, Any]], Coroutine] | None = field(default=None, repr=False)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit a domain event through the runtime event bus.

        Args:
            event_type: Dot-delimited event name, e.g. "contacts.created"
            payload: Arbitrary event data.
        """
        if self._emit is not None:
            await self._emit(event_type, payload)
