from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from mozaiks.core.persistence.models import RunCheckpointRecord
from mozaiks.core.persistence.ports import CheckpointStorePort
from mozaiks.core.db.session import session_scope


class InMemoryCheckpointStore(CheckpointStorePort):
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def save_checkpoint(self, *, run_id: str, checkpoint_key: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._items[(run_id, checkpoint_key)] = dict(payload)

    async def load_checkpoint(self, *, run_id: str, checkpoint_key: str) -> dict[str, Any] | None:
        async with self._lock:
            value = self._items.get((run_id, checkpoint_key))
            return None if value is None else dict(value)


class SqlAlchemyCheckpointStore(CheckpointStorePort):
    async def save_checkpoint(self, *, run_id: str, checkpoint_key: str, payload: dict[str, Any]) -> None:
        async with session_scope() as session:
            existing = await session.scalar(
                select(RunCheckpointRecord).where(
                    RunCheckpointRecord.run_id == run_id,
                    RunCheckpointRecord.checkpoint_key == checkpoint_key,
                )
            )
            if existing is None:
                session.add(
                    RunCheckpointRecord(
                        run_id=run_id,
                        checkpoint_key=checkpoint_key,
                        payload_json=dict(payload),
                    )
                )
            else:
                existing.payload_json = dict(payload)

    async def load_checkpoint(self, *, run_id: str, checkpoint_key: str) -> dict[str, Any] | None:
        async with session_scope() as session:
            existing = await session.scalar(
                select(RunCheckpointRecord).where(
                    RunCheckpointRecord.run_id == run_id,
                    RunCheckpointRecord.checkpoint_key == checkpoint_key,
                )
            )
        if existing is None:
            return None
        return dict(existing.payload_json or {})
