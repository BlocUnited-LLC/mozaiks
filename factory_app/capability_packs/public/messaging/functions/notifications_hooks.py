from __future__ import annotations

from typing import Any, Dict, List

from factory_app.app.modules.communications.backend.notifications import (
    resolve_thread_recipients as _resolve_thread_recipients,
)


async def resolve_thread_recipients(event: Dict[str, Any]) -> List[str]:
    return await _resolve_thread_recipients(event)


__all__ = ["resolve_thread_recipients"]
