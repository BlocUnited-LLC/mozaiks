from __future__ import annotations

"""ModuleContext — runtime context injected into every module action handler.

Module handlers never reach into the request cycle themselves. The runtime
builds this context from the incoming request and injects it so that:
  - Handlers are testable with a mock context
  - Auth/tenant info is never pulled from globals
  - Event emission goes through one consistent path
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional


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
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None

    # Request tracing
    correlation_id: Optional[str] = None

    # Auth token forwarded from the incoming request (for external API calls)
    auth_token: Optional[str] = None

    # Setting definitions declared in settings.yaml for this module.
    # Each entry is a setting definition dict: {id, type, default, label, ...}.
    # Handlers use this to resolve defaults or validate setting-aware logic.
    settings: Optional[List[Dict[str, Any]]] = None

    # Event emitter — async callable(event_type, payload) -> None
    # Injected by ModuleExecutor; no-op if not wired.
    _emit: Optional[Callable[[str, Dict[str, Any]], Coroutine]] = field(default=None, repr=False)

    async def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit a domain event through the runtime event bus.

        Args:
            event_type: Dot-delimited event name, e.g. "contacts.created"
            payload: Arbitrary event data.
        """
        if self._emit is not None:
            await self._emit(event_type, payload)
