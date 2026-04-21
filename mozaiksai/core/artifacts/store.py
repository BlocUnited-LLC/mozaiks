from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from pymongo import ReturnDocument

from logs.logging_config import get_workflow_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id

from .models import (
    ArtifactCommitMetadata,
    ArtifactFileManifestEntry,
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    ChangeClassification,
    ChangeRequestDoc,
    RefinementSessionDoc,
    RefinementSessionStatus,
)

logger = get_workflow_logger("artifact_store")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ArtifactStore:
    """Canonical artifact persistence for versioned build/refinement state."""

    def __init__(self) -> None:
        self.client: Optional[Any] = None
        self._init_lock = asyncio.Lock()

    async def _ensure_client(self) -> None:
        if self.client is not None:
            return
        async with self._init_lock:
            if self.client is not None:
                return
            self.client = get_mongo_client()
            await self._ensure_indexes()

    async def _ensure_indexes(self) -> None:
        versions = await self._coll("ArtifactVersions")
        await versions.create_index(
            [("app_id", 1), ("artifact_kind", 1), ("artifact_key", 1), ("version_number", -1)],
            name="av_app_kind_key_version",
            unique=True,
        )
        await versions.create_index(
            [("app_id", 1), ("lineage_root_id", 1), ("created_at", -1)],
            name="av_app_lineage_created",
        )
        await versions.create_index(
            [("app_id", 1), ("lifecycle_status", 1), ("updated_at", -1)],
            name="av_app_status_updated",
        )

        counters = await self._coll("ArtifactVersionCounters")
        await counters.create_index(
            [("app_id", 1), ("artifact_kind", 1), ("artifact_key", 1)],
            name="avc_app_kind_key",
            unique=True,
        )

        change_requests = await self._coll("ChangeRequests")
        await change_requests.create_index(
            [("app_id", 1), ("artifact_version_id", 1), ("created_at", -1)],
            name="cr_app_version_created",
        )
        await change_requests.create_index(
            [("app_id", 1), ("classification", 1), ("created_at", -1)],
            name="cr_app_class_created",
        )

        refinement_sessions = await self._coll("RefinementSessions")
        await refinement_sessions.create_index(
            [("app_id", 1), ("artifact_version_id", 1), ("started_at", -1)],
            name="rs_app_version_started",
        )
        await refinement_sessions.create_index(
            [("app_id", 1), ("sandbox_id", 1)],
            name="rs_app_sandbox",
            sparse=True,
        )

    async def _coll(self, name: str):
        await self._ensure_client()
        return self.client["mozaiksai"][name]

    async def _next_version_number(self, *, app_id: str, artifact_kind: str, artifact_key: str) -> int:
        counters = await self._coll("ArtifactVersionCounters")
        now = _utc_now()
        counter_id = f"{app_id}:{artifact_kind}:{artifact_key}"
        doc = await counters.find_one_and_update(
            {"_id": counter_id, **build_app_scope_filter(app_id)},
            {
                "$inc": {"sequence": 1},
                "$setOnInsert": {
                    "_id": counter_id,
                    "app_id": app_id,
                    "artifact_kind": artifact_kind,
                    "artifact_key": artifact_key,
                    "created_at": now,
                },
                "$set": {"updated_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int((doc or {}).get("sequence") or 1)

    async def create_artifact_version(
        self,
        *,
        app_id: str,
        artifact_kind: str,
        artifact_key: str,
        files_manifest: Optional[Iterable[Dict[str, Any] | ArtifactFileManifestEntry]] = None,
        source_workflow: Optional[str] = None,
        source_chat_id: Optional[str] = None,
        parent_version_id: Optional[str] = None,
        canonical_inputs_version: Optional[Dict[str, str]] = None,
        lifecycle_status: ArtifactLifecycleStatus = ArtifactLifecycleStatus.CURRENT,
        validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PENDING,
        commit_metadata: Optional[Dict[str, Any] | ArtifactCommitMetadata] = None,
    ) -> ArtifactVersionDoc:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")

        versions = await self._coll("ArtifactVersions")
        parent_doc: Optional[ArtifactVersionDoc] = None
        if parent_version_id:
            parent_raw = await versions.find_one(
                {"_id": parent_version_id, **build_app_scope_filter(resolved_app_id)}
            )
            if not isinstance(parent_raw, dict):
                raise ValueError(f"Unknown parent_version_id: {parent_version_id}")
            parent_doc = ArtifactVersionDoc.model_validate(parent_raw)

        artifact_version_id = f"av_{uuid4().hex[:24]}"
        version_number = await self._next_version_number(
            app_id=resolved_app_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
        )
        lineage_root_id = parent_doc.lineage_root_id if parent_doc else artifact_version_id
        manifest_entries = [
            entry if isinstance(entry, ArtifactFileManifestEntry) else ArtifactFileManifestEntry.model_validate(entry)
            for entry in (files_manifest or [])
        ]
        commit_doc = (
            commit_metadata
            if isinstance(commit_metadata, ArtifactCommitMetadata)
            else ArtifactCommitMetadata.model_validate(commit_metadata or {})
        )
        now = _utc_now()
        version_doc = ArtifactVersionDoc(
            _id=artifact_version_id,
            app_id=resolved_app_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
            version_number=version_number,
            parent_version_id=parent_version_id,
            lineage_root_id=lineage_root_id,
            source_workflow=source_workflow,
            source_chat_id=source_chat_id,
            canonical_inputs_version=dict(canonical_inputs_version or {}),
            lifecycle_status=lifecycle_status,
            validation_status=validation_status,
            files_manifest=manifest_entries,
            commit_metadata=commit_doc,
            created_at=now,
            updated_at=now,
        )

        if lifecycle_status == ArtifactLifecycleStatus.CURRENT:
            await versions.update_many(
                {
                    "app_id": resolved_app_id,
                    "artifact_kind": artifact_kind,
                    "artifact_key": artifact_key,
                    "lifecycle_status": ArtifactLifecycleStatus.CURRENT.value,
                },
                {
                    "$set": {
                        "lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED.value,
                        "updated_at": now,
                    }
                },
            )

        await versions.insert_one(version_doc.model_dump(by_alias=True, mode="python"))
        return version_doc

    async def get_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
    ) -> Optional[ArtifactVersionDoc]:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        versions = await self._coll("ArtifactVersions")
        raw = await versions.find_one({"_id": artifact_version_id, **build_app_scope_filter(resolved_app_id)})
        if not isinstance(raw, dict):
            return None
        return ArtifactVersionDoc.model_validate(raw)

    async def list_artifact_versions(
        self,
        *,
        app_id: str,
        artifact_kind: Optional[str] = None,
        artifact_key: Optional[str] = None,
        lineage_root_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[ArtifactVersionDoc]:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        query: Dict[str, Any] = {"app_id": resolved_app_id}
        if artifact_kind:
            query["artifact_kind"] = artifact_kind
        if artifact_key:
            query["artifact_key"] = artifact_key
        if lineage_root_id:
            query["lineage_root_id"] = lineage_root_id
        versions = await self._coll("ArtifactVersions")
        cursor = versions.find(query).sort([("version_number", -1), ("created_at", -1)]).limit(max(1, int(limit)))
        rows = await cursor.to_list(length=max(1, int(limit)))
        return [ArtifactVersionDoc.model_validate(row) for row in rows]

    async def mark_artifact_version_stale(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        reason: str,
        invalidated_by_version_id: Optional[str] = None,
    ) -> bool:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        versions = await self._coll("ArtifactVersions")
        now = _utc_now()
        result = await versions.update_one(
            {
                "_id": artifact_version_id,
                **build_app_scope_filter(resolved_app_id),
                "lifecycle_status": {"$nin": [ArtifactLifecycleStatus.ARCHIVED.value, ArtifactLifecycleStatus.DELETED.value]},
            },
            {
                "$set": {
                    "lifecycle_status": ArtifactLifecycleStatus.STALE.value,
                    "invalidation_reason": reason,
                    "invalidated_by_version_id": invalidated_by_version_id,
                    "stale_at": now,
                    "updated_at": now,
                }
            },
        )
        return bool(result.modified_count)

    async def invalidate_artifact_family(
        self,
        *,
        app_id: str,
        artifact_kind: str,
        artifact_key: str,
        reason: str,
        invalidated_by_version_id: Optional[str] = None,
        exclude_version_id: Optional[str] = None,
    ) -> int:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        query: Dict[str, Any] = {
            "app_id": resolved_app_id,
            "artifact_kind": artifact_kind,
            "artifact_key": artifact_key,
            "lifecycle_status": {"$nin": [ArtifactLifecycleStatus.ARCHIVED.value, ArtifactLifecycleStatus.DELETED.value, ArtifactLifecycleStatus.STALE.value]},
        }
        if exclude_version_id:
            query["_id"] = {"$ne": exclude_version_id}
        versions = await self._coll("ArtifactVersions")
        now = _utc_now()
        result = await versions.update_many(
            query,
            {
                "$set": {
                    "lifecycle_status": ArtifactLifecycleStatus.STALE.value,
                    "invalidation_reason": reason,
                    "invalidated_by_version_id": invalidated_by_version_id,
                    "stale_at": now,
                    "updated_at": now,
                }
            },
        )
        return int(result.modified_count)

    async def set_validation_status(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        validation_status: ArtifactValidationStatus,
        lifecycle_status: Optional[ArtifactLifecycleStatus] = None,
        commit_metadata: Optional[Dict[str, Any] | ArtifactCommitMetadata] = None,
    ) -> bool:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        updates: Dict[str, Any] = {
            "validation_status": validation_status.value,
            "updated_at": _utc_now(),
        }
        if lifecycle_status is not None:
            updates["lifecycle_status"] = lifecycle_status.value
        if commit_metadata is not None:
            commit_doc = (
                commit_metadata
                if isinstance(commit_metadata, ArtifactCommitMetadata)
                else ArtifactCommitMetadata.model_validate(commit_metadata)
            )
            updates["commit_metadata"] = commit_doc.model_dump(mode="python")

        versions = await self._coll("ArtifactVersions")
        result = await versions.update_one(
            {"_id": artifact_version_id, **build_app_scope_filter(resolved_app_id)},
            {"$set": updates},
        )
        return bool(result.modified_count)

    async def create_change_request(
        self,
        *,
        app_id: str,
        artifact_kind: str,
        artifact_key: str,
        artifact_version_id: str,
        raw_user_request: str,
        classification: ChangeClassification,
        scope: Optional[Dict[str, Any]] = None,
        router_decision: Optional[Dict[str, Any]] = None,
        created_by_user_id: Optional[str] = None,
    ) -> ChangeRequestDoc:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        doc = ChangeRequestDoc(
            _id=f"cr_{uuid4().hex[:24]}",
            app_id=resolved_app_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
            artifact_version_id=artifact_version_id,
            raw_user_request=raw_user_request,
            classification=classification,
            scope=dict(scope or {}),
            router_decision=dict(router_decision or {}),
            created_by_user_id=created_by_user_id,
        )
        coll = await self._coll("ChangeRequests")
        await coll.insert_one(doc.model_dump(by_alias=True, mode="python"))
        return doc

    async def create_refinement_session(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        change_request_id: str,
        provider: str = "e2b",
        sandbox_id: Optional[str] = None,
        status: RefinementSessionStatus = RefinementSessionStatus.PROVISIONING,
        preview_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RefinementSessionDoc:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        doc = RefinementSessionDoc(
            _id=f"rs_{uuid4().hex[:24]}",
            app_id=resolved_app_id,
            artifact_version_id=artifact_version_id,
            change_request_id=change_request_id,
            provider=provider,
            sandbox_id=sandbox_id,
            status=status,
            preview_url=preview_url,
            metadata=dict(metadata or {}),
        )
        coll = await self._coll("RefinementSessions")
        await coll.insert_one(doc.model_dump(by_alias=True, mode="python"))
        return doc

    async def update_refinement_session(
        self,
        *,
        app_id: str,
        session_id: str,
        status: Optional[RefinementSessionStatus] = None,
        sandbox_id: Optional[str] = None,
        preview_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ended_at: Optional[datetime] = None,
    ) -> bool:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        updates: Dict[str, Any] = {"started_at": {"$exists": True}}
        set_doc: Dict[str, Any] = {"metadata": dict(metadata or {})} if metadata is not None else {}
        if status is not None:
            set_doc["status"] = status.value
        if sandbox_id is not None:
            set_doc["sandbox_id"] = sandbox_id
        if preview_url is not None:
            set_doc["preview_url"] = preview_url
        if ended_at is not None:
            set_doc["ended_at"] = ended_at
        if not set_doc:
            return False

        coll = await self._coll("RefinementSessions")
        result = await coll.update_one(
            {"_id": session_id, **build_app_scope_filter(resolved_app_id)},
            {"$set": set_doc},
        )
        return bool(result.modified_count)


_artifact_store: Optional[ArtifactStore] = None


def get_artifact_store() -> ArtifactStore:
    global _artifact_store
    if _artifact_store is None:
        _artifact_store = ArtifactStore()
    return _artifact_store


__all__ = ["ArtifactStore", "get_artifact_store"]