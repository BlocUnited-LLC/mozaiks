from __future__ import annotations

from typing import Any, Dict, Optional, Set, Type

import pytest

from mozaiksai.core.workflow.stream.handlers.base import BaseEventHandler
from mozaiksai.core.workflow.stream.registry import EventHandlerRegistry
from mozaiksai.core.workflow.stream.handlers.transition_handler import TransitionHandler


class _EmptyHandler(BaseEventHandler):
    def event_types(self) -> Set[Type]:
        return set()

    async def handle(self, event: Any, ctx: Any, state: Any) -> Optional[Dict[str, Any]]:
        return None


def test_registry_rejects_empty_event_type_handlers() -> None:
    registry = EventHandlerRegistry()

    with pytest.raises(ValueError, match="must declare at least one event type"):
        registry.register(_EmptyHandler())


def test_registry_rejects_duplicate_event_type_handlers() -> None:
    registry = EventHandlerRegistry()

    registry.register(TransitionHandler())

    with pytest.raises(ValueError, match="Duplicate handler registration"):
        registry.register(TransitionHandler())