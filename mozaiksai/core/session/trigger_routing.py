from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .model import SessionLifecycle, TriggerInput


@dataclass(slots=True)
class TriggerRoutingContribution:
    workflow_id: Optional[str] = None
    context_seed: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    is_full_restart: bool = False
    lifecycle_state: SessionLifecycle = SessionLifecycle.ACTIVE


class TriggerRouteResolver(Protocol):
    def resolve(self, trigger: TriggerInput) -> Optional[TriggerRoutingContribution]: ...


class NullTriggerRouteResolver:
    def resolve(self, trigger: TriggerInput) -> Optional[TriggerRoutingContribution]:
        return None