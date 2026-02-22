from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mozaiks.core.persistence.ports import PersistedEvent


class RunStreamHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[PersistedEvent]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, *, run_id: str, event: PersistedEvent) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(run_id, set()))
        for queue in queues:
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, run_id: str) -> AsyncIterator[asyncio.Queue[PersistedEvent]]:
        queue: asyncio.Queue[PersistedEvent] = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(run_id, set()).add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(run_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(run_id, None)
