"""ModuleContext — runtime context injected into every module action handler.

Module handlers never reach into the request cycle themselves. The runtime
builds this context from the incoming request and injects it so that:
  - Handlers are testable with a mock context
  - Auth/tenant info is never pulled from globals
  - Event emission goes through one consistent path
  - Generated module repos can use ctx.persistence for app-scoped storage
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mozaiksai.core.runtime.composition.module_authority import (
    ModuleDispatchAudit,
    ModuleDispatchAuthority,
    ModuleDispatchProvenance,
)
from mozaiksai.core.runtime.composition.module_event_provenance import ModuleEventRejection
from mozaiksai.core.runtime.persistence.adapter import ModulePersistenceContext

if TYPE_CHECKING:
    from mozaiksai.core.events.unified_event_dispatcher import EventDispatchOutcome


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
    module_id: str | None = None
    action_id: str | None = None

    # Request tracing
    correlation_id: str | None = None

    # Auth token forwarded from the incoming request (for external API calls)
    auth_token: str | None = None

    # Resolved permission ids for this caller/action dispatch.  None preserves
    # trusted internal runtime calls; external dispatch normally supplies a list.
    permissions: list[str] | None = None
    dispatch_authority: ModuleDispatchAuthority | None = None
    dispatch_provenance: ModuleDispatchProvenance | None = None
    dispatch_audit: ModuleDispatchAudit | None = None

    # Resolved setting values for this module, scoped to the calling user.
    # Dict of {setting_id: resolved_value} — already merged from defaults,
    # app-scoped overrides, and user-scoped overrides.
    # Empty dict when the module declares no settings; None for trusted internal calls.
    settings: dict[str, Any] | None = None

    # ModuleExecutor binds collection ownership to the authenticated persistence
    # principal and data contract, independently of the selectable scope above.
    # This may be None in explicit test contexts or calls without app_id.
    # ctx.db is intentionally not provided.
    persistence: ModulePersistenceContext | None = None

    # Event emitter — async callable(event_type, payload) -> delivery or rejection.
    # Injected by ModuleExecutor; no-op if not wired.
    _emit: Callable[
        [str, dict[str, Any]], Awaitable[EventDispatchOutcome | ModuleEventRejection | None]
    ] | None = field(
        default=None, repr=False,
    )
    _metrics: Any | None = field(default=None, repr=False)

    @property
    def metrics(self) -> Any:
        """Durable app metric tracker for host-neutral usage signals."""
        if self._metrics is None:
            from mozaiksai.core.metrics import AppMetrics

            self._metrics = AppMetrics(self)
        return self._metrics

    async def emit(
        self, event_type: str, payload: dict[str, Any]
    ) -> EventDispatchOutcome | ModuleEventRejection | None:
        """Emit a domain event through the runtime event bus.

        Returns an ``EventDispatchOutcome`` when the runtime bus is wired, or a
        ``ModuleEventRejection`` when the runtime refused it: this action does
        not declare the event in module.yaml ``emits``, the payload fails the
        event's declared ``payload_schema``, or that schema cannot be
        evaluated. A refused event is never dispatched, so no reaction or
        notification runs for it, yet emit still returns normally: the action's
        writes may already be committed, and the dispatch result and audit
        name the rejection too. Code that must know whether the event went out
        (an outbox that marks it delivered, for example) checks the rejection
        and then the required reaction's outcome. A test double may still
        return ``None`` for an event it accepted.

        The checks and the rejection belong to the context ModuleExecutor
        builds for a dispatched action. With no event bus wired, emitting does
        nothing and returns ``None``; a context a caller passes to
        ``ModuleExecutor.execute`` keeps its own emitter.

        Args:
            event_type: Dot-delimited event name, e.g. "domain.contacts.created"
            payload: Event data, checked against the event's payload_schema.
        """
        if self._emit is None:
            return None
        return await self._emit(event_type, payload)
