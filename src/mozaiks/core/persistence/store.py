from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, func, select

from mozaiks.core.persistence.models import RunArtifactRecord, RunEventRecord, RunRecord
from mozaiks.core.persistence.ports import ArtifactRecordView, EventStorePort, PersistedEvent, RunRecordView
from mozaiks.contracts import ARTIFACT_CREATED_EVENT_TYPE, EventEnvelope
from mozaiks.core.db.session import session_scope


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_ts(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _to_run_record_view(record: RunRecord) -> RunRecordView:
    return RunRecordView(
        run_id=record.run_id,
        created_at=record.created_at,
        status=record.status,
        workflow_name=record.workflow_name,
        workflow_version=record.workflow_version,
        metadata=dict(record.metadata_json or {}),
        last_seq=int(record.last_seq or 0),
    )


def _coerce_metadata(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None


def _resequence_event(*, run_id: str, seq: int, event: EventEnvelope) -> EventEnvelope:
    return EventEnvelope(
        event_type=event.event_type,
        seq=seq,
        occurred_at=_normalize_ts(event.occurred_at),
        run_id=run_id,
        schema_version=event.schema_version,
        payload=dict(event.payload or {}),
        metadata=_coerce_metadata(event.metadata),
    )


def _as_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    normalized = str(value).strip()
    return normalized or None


@dataclass(frozen=True)
class _ArtifactCandidate:
    artifact_id: str
    artifact_type: str
    uri: str
    checksum: str
    version: str
    media_type: str | None
    content_base64: str | None
    metadata: dict[str, Any] | None


def _artifact_candidate_from_payload(payload: dict[str, Any]) -> _ArtifactCandidate | None:
    nested = payload.get("artifact")
    nested_obj = nested if isinstance(nested, dict) else {}
    nested_meta = payload.get("metadata")
    nested_meta_obj = nested_meta if isinstance(nested_meta, dict) else {}

    uri = (
        _as_string(payload.get("uri"))
        or _as_string(payload.get("artifact_uri"))
        or _as_string(nested_obj.get("artifact_uri"))
        or _as_string(nested_obj.get("uri"))
    )
    checksum = _as_string(payload.get("checksum")) or _as_string(nested_obj.get("checksum"))
    if uri is None or checksum is None:
        return None

    artifact_id = (
        _as_string(payload.get("artifact_id"))
        or _as_string(nested_obj.get("artifact_id"))
        or str(uuid4())
    )
    artifact_type = (
        _as_string(payload.get("artifact_type"))
        or _as_string(nested_meta_obj.get("artifact_type"))
        or "generic"
    )
    media_type = _as_string(payload.get("media_type")) or _as_string(nested_obj.get("media_type"))
    version = _as_string(payload.get("version")) or "1.0.0"

    content_base64 = _as_string(payload.get("content_base64"))
    if content_base64 is None:
        inline_content = payload.get("content")
        if isinstance(inline_content, str):
            content_base64 = base64.b64encode(inline_content.encode("utf-8")).decode("ascii")

    return _ArtifactCandidate(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        uri=uri,
        checksum=checksum,
        version=version,
        media_type=media_type,
        content_base64=content_base64,
        metadata=nested_meta_obj or None,
    )


def _artifact_view(record: RunArtifactRecord) -> ArtifactRecordView:
    return ArtifactRecordView(
        artifact_id=record.artifact_id,
        run_id=record.run_id,
        seq=record.seq,
        artifact_type=record.artifact_type,
        uri=record.uri,
        checksum=record.checksum,
        version=record.version,
        media_type=record.media_type,
        content_base64=record.content_base64,
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
    )


def _envelope_from_row(row: RunEventRecord) -> EventEnvelope:
    return EventEnvelope(
        event_type=row.event_type,
        seq=row.seq,
        occurred_at=_normalize_ts(row.occurred_at),
        run_id=row.run_id,
        schema_version=row.schema_version,
        payload=dict(row.payload_json or {}),
        metadata=_coerce_metadata(row.metadata_json),
    )


class InMemoryEventStore(EventStorePort):
    def __init__(self) -> None:
        self._runs: dict[str, RunRecordView] = {}
        self._events: dict[str, list[PersistedEvent]] = {}
        self._artifacts_by_id: dict[str, ArtifactRecordView] = {}
        self._artifact_lookup: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def create_run(
        self,
        *,
        run_id: str,
        workflow_name: str,
        workflow_version: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecordView:
        async with self._lock:
            if run_id in self._runs:
                raise ValueError(f"Run '{run_id}' already exists")
            record = RunRecordView(
                run_id=run_id,
                created_at=_utc_now(),
                status=status,
                workflow_name=workflow_name,
                workflow_version=workflow_version,
                metadata=dict(metadata or {}),
                last_seq=0,
            )
            self._runs[run_id] = record
            self._events[run_id] = []
            return record

    async def get_run(self, run_id: str) -> RunRecordView | None:
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return None
            return RunRecordView(
                run_id=record.run_id,
                created_at=record.created_at,
                status=record.status,
                workflow_name=record.workflow_name,
                workflow_version=record.workflow_version,
                metadata=dict(record.metadata),
                last_seq=record.last_seq,
            )

    async def update_run_status(self, *, run_id: str, status: str) -> RunRecordView:
        async with self._lock:
            existing = self._runs.get(run_id)
            if existing is None:
                raise KeyError(f"Run '{run_id}' does not exist")
            updated = RunRecordView(
                run_id=existing.run_id,
                created_at=existing.created_at,
                status=status,
                workflow_name=existing.workflow_name,
                workflow_version=existing.workflow_version,
                metadata=dict(existing.metadata),
                last_seq=existing.last_seq,
            )
            self._runs[run_id] = updated
            return updated

    async def append_event(self, *, run_id: str, event: EventEnvelope) -> PersistedEvent:
        async with self._lock:
            if run_id not in self._runs:
                raise KeyError(f"Run '{run_id}' does not exist")
            seq = len(self._events[run_id]) + 1
            normalized = _resequence_event(run_id=run_id, seq=seq, event=event)
            persisted = PersistedEvent(seq=seq, event=normalized)
            self._events[run_id].append(persisted)
            existing = self._runs[run_id]
            self._runs[run_id] = RunRecordView(
                run_id=existing.run_id,
                created_at=existing.created_at,
                status=existing.status,
                workflow_name=existing.workflow_name,
                workflow_version=existing.workflow_version,
                metadata=dict(existing.metadata),
                last_seq=seq,
            )
            self._upsert_artifact_if_needed(run_id=run_id, persisted_event=persisted)
            return persisted

    async def list_events(self, *, run_id: str, after_seq: int = 0, limit: int = 100) -> list[PersistedEvent]:
        size = max(1, limit)
        async with self._lock:
            events = self._events.get(run_id, [])
            return [item for item in events if item.seq > after_seq][:size]

    async def get_latest_event(self, *, run_id: str) -> PersistedEvent | None:
        async with self._lock:
            events = self._events.get(run_id)
            if not events:
                return None
            return events[-1]

    async def get_artifact(
        self,
        *,
        artifact_id: str | None = None,
        uri: str | None = None,
        checksum: str | None = None,
    ) -> ArtifactRecordView | None:
        async with self._lock:
            record: ArtifactRecordView | None = None
            if artifact_id is not None:
                record = self._artifacts_by_id.get(artifact_id)
            elif uri is not None and checksum is not None:
                key = (uri, checksum)
                resolved_id = self._artifact_lookup.get(key)
                if resolved_id is not None:
                    record = self._artifacts_by_id.get(resolved_id)
            if record is None:
                return None
            return ArtifactRecordView(
                artifact_id=record.artifact_id,
                run_id=record.run_id,
                seq=record.seq,
                artifact_type=record.artifact_type,
                uri=record.uri,
                checksum=record.checksum,
                version=record.version,
                media_type=record.media_type,
                content_base64=record.content_base64,
                metadata=dict(record.metadata),
                created_at=record.created_at,
            )

    def _upsert_artifact_if_needed(self, *, run_id: str, persisted_event: PersistedEvent) -> None:
        if persisted_event.event.event_type != ARTIFACT_CREATED_EVENT_TYPE:
            return

        candidate = _artifact_candidate_from_payload(dict(persisted_event.event.payload or {}))
        if candidate is None:
            return

        lookup_key = (candidate.uri, candidate.checksum)
        existing_id = self._artifact_lookup.get(lookup_key)
        artifact_id = existing_id or candidate.artifact_id
        record = ArtifactRecordView(
            artifact_id=artifact_id,
            run_id=run_id,
            seq=persisted_event.seq,
            artifact_type=candidate.artifact_type,
            uri=candidate.uri,
            checksum=candidate.checksum,
            version=candidate.version,
            media_type=candidate.media_type,
            content_base64=candidate.content_base64,
            metadata=dict(candidate.metadata or {}),
            created_at=_utc_now(),
        )
        self._artifacts_by_id[artifact_id] = record
        self._artifact_lookup[lookup_key] = artifact_id


class SqlAlchemyEventStore(EventStorePort):
    async def create_run(
        self,
        *,
        run_id: str,
        workflow_name: str,
        workflow_version: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecordView:
        async with session_scope() as session:
            existing = await session.get(RunRecord, run_id)
            if existing is not None:
                raise ValueError(f"Run '{run_id}' already exists")
            record = RunRecord(
                run_id=run_id,
                status=status,
                last_seq=0,
                workflow_name=workflow_name,
                workflow_version=workflow_version,
                metadata_json=dict(metadata or {}),
            )
            session.add(record)
            await session.flush()
        return _to_run_record_view(record)

    async def get_run(self, run_id: str) -> RunRecordView | None:
        async with session_scope() as session:
            record = await session.get(RunRecord, run_id)
        if record is None:
            return None
        return _to_run_record_view(record)

    async def update_run_status(self, *, run_id: str, status: str) -> RunRecordView:
        async with session_scope() as session:
            record = await session.get(RunRecord, run_id)
            if record is None:
                raise KeyError(f"Run '{run_id}' does not exist")
            record.status = status
            await session.flush()
        return _to_run_record_view(record)

    async def append_event(self, *, run_id: str, event: EventEnvelope) -> PersistedEvent:
        async with session_scope() as session:
            process = await session.get(RunRecord, run_id)
            if process is None:
                raise KeyError(f"Run '{run_id}' does not exist")

            result = await session.execute(
                select(func.coalesce(func.max(RunEventRecord.seq), 0)).where(RunEventRecord.run_id == run_id)
            )
            next_seq = int(result.scalar_one()) + 1
            normalized = _resequence_event(run_id=run_id, seq=next_seq, event=event)
            process.last_seq = next_seq

            row = RunEventRecord(
                run_id=run_id,
                seq=next_seq,
                occurred_at=normalized.occurred_at,
                event_type=normalized.event_type,
                schema_version=normalized.schema_version,
                payload_json=dict(normalized.payload or {}),
                metadata_json=_coerce_metadata(normalized.metadata),
            )
            session.add(row)
            await session.flush()

            await self._upsert_artifact_if_needed(
                session=session,
                run_id=run_id,
                seq=next_seq,
                event=normalized,
            )

        return PersistedEvent(seq=next_seq, event=normalized)

    async def list_events(self, *, run_id: str, after_seq: int = 0, limit: int = 100) -> list[PersistedEvent]:
        size = max(1, limit)
        async with session_scope() as session:
            result = await session.execute(
                select(RunEventRecord)
                .where(RunEventRecord.run_id == run_id, RunEventRecord.seq > after_seq)
                .order_by(RunEventRecord.seq.asc())
                .limit(size)
            )
            rows = list(result.scalars().all())
        return [PersistedEvent(seq=row.seq, event=_envelope_from_row(row)) for row in rows]

    async def get_latest_event(self, *, run_id: str) -> PersistedEvent | None:
        async with session_scope() as session:
            result = await session.execute(
                select(RunEventRecord)
                .where(RunEventRecord.run_id == run_id)
                .order_by(desc(RunEventRecord.seq))
                .limit(1)
            )
            row = result.scalar_one_or_none()
        if row is None:
            return None
        return PersistedEvent(seq=row.seq, event=_envelope_from_row(row))

    async def get_artifact(
        self,
        *,
        artifact_id: str | None = None,
        uri: str | None = None,
        checksum: str | None = None,
    ) -> ArtifactRecordView | None:
        async with session_scope() as session:
            if artifact_id is not None:
                row = await session.get(RunArtifactRecord, artifact_id)
                return None if row is None else _artifact_view(row)

            if uri is None or checksum is None:
                return None

            row = await session.scalar(
                select(RunArtifactRecord).where(
                    RunArtifactRecord.uri == uri,
                    RunArtifactRecord.checksum == checksum,
                )
            )
            return None if row is None else _artifact_view(row)

    async def _upsert_artifact_if_needed(
        self,
        *,
        session: Any,
        run_id: str,
        seq: int,
        event: EventEnvelope,
    ) -> None:
        if event.event_type != ARTIFACT_CREATED_EVENT_TYPE:
            return

        candidate = _artifact_candidate_from_payload(dict(event.payload or {}))
        if candidate is None:
            return

        existing = await session.scalar(
            select(RunArtifactRecord).where(
                RunArtifactRecord.uri == candidate.uri,
                RunArtifactRecord.checksum == candidate.checksum,
            )
        )
        if existing is None:
            session.add(
                RunArtifactRecord(
                    artifact_id=candidate.artifact_id,
                    run_id=run_id,
                    seq=seq,
                    artifact_type=candidate.artifact_type,
                    uri=candidate.uri,
                    checksum=candidate.checksum,
                    version=candidate.version,
                    media_type=candidate.media_type,
                    content_base64=candidate.content_base64,
                    metadata_json=dict(candidate.metadata or {}),
                )
            )
            return

        existing.run_id = run_id
        existing.seq = seq
        existing.artifact_type = candidate.artifact_type
        existing.version = candidate.version
        existing.media_type = candidate.media_type
        existing.content_base64 = candidate.content_base64
        existing.metadata_json = dict(candidate.metadata or {})
