from __future__ import annotations

from typing import Dict


class CommunicationsRepo:
    """Persistence scaffold for the communications capability pack.

    The pack ships app-overlay contracts and backend layer shapes. A promoted app
    workspace is expected to replace these pass-through methods with real durable
    storage and query logic.
    """

    async def create_thread_record(self, thread: Dict[str, object]) -> Dict[str, object]:
        return dict(thread)

    async def create_message_record(self, message: Dict[str, object]) -> Dict[str, object]:
        return dict(message)

    async def update_read_state(self, read_state: Dict[str, object]) -> Dict[str, object]:
        return dict(read_state)

    async def create_announcement_record(self, announcement: Dict[str, object]) -> Dict[str, object]:
        return dict(announcement)


__all__ = ["CommunicationsRepo"]