from __future__ import annotations

from typing import Any, Dict, Optional

from .service import CommunicationsService


class CommunicationsModule:
    """Thin action handler for the communications module."""

    def __init__(self, service: Optional[CommunicationsService] = None) -> None:
        self.service = service or CommunicationsService()

    async def create_thread(self, payload: Dict[str, Any], *, actor_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a standalone messaging thread."""
        return await self.service.create_thread(payload, actor_id=actor_id)

    async def send_message(self, payload: Dict[str, Any], *, actor_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a message into an existing thread."""
        return await self.service.send_message(payload, actor_id=actor_id)

    async def mark_thread_read(self, payload: Dict[str, Any], *, actor_id: Optional[str] = None) -> Dict[str, Any]:
        """Update read state for the caller in a thread."""
        return await self.service.mark_thread_read(payload, actor_id=actor_id)

    async def post_announcement(self, payload: Dict[str, Any], *, actor_id: Optional[str] = None) -> Dict[str, Any]:
        """Broadcast an announcement to a configured audience."""
        return await self.service.post_announcement(payload, actor_id=actor_id)


__all__ = ["CommunicationsModule"]