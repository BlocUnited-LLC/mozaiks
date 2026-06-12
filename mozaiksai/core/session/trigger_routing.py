from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .model import SessionLifecycle, TriggerInput


@dataclass(slots=True)
class TriggerRoutingContribution:
    workflow_id: str | None = None
    journey_id: str | None = None
    context_seed: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    is_full_restart: bool = False
    lifecycle_state: SessionLifecycle = SessionLifecycle.ACTIVE


class TriggerRouteResolver(Protocol):
    async def resolve(self, trigger: TriggerInput) -> TriggerRoutingContribution | None: ...


class NullTriggerRouteResolver:
    async def resolve(self, trigger: TriggerInput) -> TriggerRoutingContribution | None:
        return None
