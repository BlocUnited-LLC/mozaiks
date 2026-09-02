from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument

from logs.logging_config import get_workflow_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id

from .models import (
    ArtifactCommitMetadata,
    BuildRecord,
    BuildRecordFileEntry,
    BuildRecordStatus,
    BuildRecordValidationStatus,
    ChangeClassification,
    ChangeIntentDoc,
    ChangeRequestDoc,
    ImpactSetDoc,
    RefinementRequestPayload,
    RefinementSessionDoc,
    RefinementSessionStatus,
)

# Prior-api aliases used internally in this module
ArtifactFileManifestEntry = BuildRecordFileEntry
ArtifactLifecycleStatus = BuildRecordStatus
ArtifactValidationStatus = BuildRecordValidationStatus

logger = get_workflow_logger("artifact_store")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BuildRecordStore:
    """Canonical build record persistence for versioned build/refinement state."""

    def __init__(self) -> None:
        self.client: Any | None = None
        self._init_lock = asyncio.Lock()

    async def _ensure_client(self) -> None:
        if self.client is not None:
            return
        async with self._init_lock:
            if self.client is not None:
                return
            self.client = get_mongo_client()
            await self._ensure_indexes()

    # Retired unique indexes keyed on fields BuildRecord documents never carry
    # (every record produced null tuples, so distinct build families collided
    # with E11000). Dropped by exact name before canonical materialization.
    _RETIRED_INDEXES: dict[str, tuple[str, ...]] = {
        "ArtifactVersions": ("av_app_kind_key_version",),
        "ArtifactVersionCounters": ("avc_app_kind_key",),
    }

    # BuildRecord identity is (app_id, build_family, build_key, version_number);
    # the model carries app scope as app_id only (build_app_scope_filter).
    _INDEX_CONTRACT: dict[str, list[dict[str, Any]]] = {
        "ArtifactVersions": [
            {
                "name": "av_app_family_key_version",
                "keys": [
                    ("app_id", 1),
                    ("build_family", 1),
                    ("build_key", 1),
                    ("version_number", -1),
                ],
                "unique": True,
            },
            {
                "name": "av_app_lineage_created",
                "keys": [("app_id", 1), ("lineage_root_id", 1), ("created_at", -1)],
            },
            {
                "name": "av_app_status_updated",
                "keys": [("app_id", 1), ("lifecycle_status", 1), ("updated_at", -1)],
            },
        ],
        "ArtifactVersionCounters": [
            {
                "name": "avc_app_family_key",
                "keys": [("app_id", 1), ("build_family", 1), ("build_key", 1)],
                "unique": True,
            },
        ],
        "ChangeRequests": [
            {
                "name": "cr_app_record_created",
                "keys": [("app_id", 1), ("build_record_id", 1), ("created_at", -1)],
            },
            {
                "name": "cr_app_class_created",
                "keys": [("app_id", 1), ("classification", 1), ("created_at", -1)],
            },
        ],
        "RefinementSessions": [
            {
                "name": "rs_app_record_started",
                "keys": [("app_id", 1), ("build_record_id", 1), ("started_at", -1)],
            },
            {
                "name": "rs_app_result_record_started",
                "keys": [("app_id", 1), ("result_build_record_id", 1), ("started_at", -1)],
                "sparse": True,
            },
            {
                "name": "rs_app_sandbox",
                "keys": [("app_id", 1), ("sandbox_id", 1)],
                "sparse": True,
            },
        ],
    }

    async def _ensure_indexes(self) -> None:
        """Materialize the store's index contract through the canonical verifier.

        Any pre-existing index whose definition conflicts with the declared
        contract fails the store closed rather than being accepted by name.
        """
        from mozaiksai.core.runtime.persistence.indexes import (
            _ensure_raw_collection_indexes,
        )

        for collection_name, retired_names in self._RETIRED_INDEXES.items():
            collection = await self._coll(collection_name)
            existing = {
                str(row.get("name") or "")
                for row in await collection.list_indexes().to_list(length=None)
            }
            for retired in retired_names:
                if retired in existing:
                    await collection.drop_index(retired)

        for collection_name, specs in self._INDEX_CONTRACT.items():
            collection = await self._coll(collection_name)
            await _ensure_raw_collection_indexes(
                collection,
                specs,
                collection_label=collection_name,
            )

    async def _coll(self, name: str):
        await self._ensure_client()
        return self.client["mozaiksai"][name]  # type: ignore[index]

    async def _next_build_record_version_number(self, *, app_id: str, build_family: str, build_key: str) -> int:
        counters = await self._coll("ArtifactVersionCounters")
        now = _utc_now()
        counter_id = f"{app_id}:{build_family}:{build_key}"
        doc = await counters.find_one_and_update(
            {"_id": counter_id, **build_app_scope_filter(app_id)},
            {
                "$inc": {"sequence": 1},
                "$setOnInsert": {
                    "_id": counter_id,
                    "app_id": app_id,
                    "build_family": build_family,
                    "build_key": build_key,
                    "created_at": now,
                },
                "$set": {"updated_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int((doc or {}).get("sequence") or 1)

    async def mark_artifact_version_stale(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        reason: str,
        invalidated_by_version_id: str | None = None,
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
        build_family: str,
        build_key: str | None = None,
        reason: str,
        invalidated_by_version_id: str | None = None,
        exclude_version_id: str | None = None,
    ) -> int:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        query: dict[str, Any] = {
            "app_id": resolved_app_id,
            "build_family": build_family,
            "lifecycle_status": {"$nin": [ArtifactLifecycleStatus.ARCHIVED.value, ArtifactLifecycleStatus.DELETED.value, ArtifactLifecycleStatus.STALE.value]},
        }
        if build_key is not None:
            query["build_key"] = build_key
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

    async def invalidate_artifact_version_refs(
        self,
        *,
        app_id: str,
        artifact_version_refs: dict[str, str],
        affected_build_families: Iterable[str] | None = None,
        reason: str,
        invalidated_by_version_id: str | None = None,
    ) -> list[str]:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")

        normalized_refs: dict[str, str] = {}
        for raw_family, raw_version_id in dict(artifact_version_refs or {}).items():
            build_family = str(raw_family or "").strip()
            artifact_version_id = str(raw_version_id or "").strip()
            if build_family and artifact_version_id:
                normalized_refs[build_family] = artifact_version_id

        target_families: list[str] = []
        raw_target_families = list(affected_build_families or normalized_refs.keys())
        for raw_family in raw_target_families:
            build_family = str(raw_family or "").strip()
            if build_family and build_family not in target_families:
                target_families.append(build_family)

        invalidated_ids: list[str] = []
        seen_version_ids: set[str] = set()
        for build_family in target_families:
            artifact_version_id = normalized_refs.get(build_family)  # type: ignore[assignment]
            if not artifact_version_id or artifact_version_id in seen_version_ids:
                continue
            seen_version_ids.add(artifact_version_id)
            if await self.mark_artifact_version_stale(
                app_id=resolved_app_id,
                artifact_version_id=artifact_version_id,
                reason=reason,
                invalidated_by_version_id=invalidated_by_version_id,
            ):
                invalidated_ids.append(artifact_version_id)
        return invalidated_ids

    async def set_validation_status(
        self,
        *,
        app_id: str,
        artifact_version_id: str | None = None,
        build_record_id: str | None = None,
        validation_status: ArtifactValidationStatus,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        commit_metadata: dict[str, Any] | ArtifactCommitMetadata | None = None,
    ) -> bool:
        resolved_id = str(build_record_id or artifact_version_id or "").strip()
        if not resolved_id:
            raise ValueError("artifact_version_id or build_record_id is required")
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        updates: dict[str, Any] = {
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
            {"_id": resolved_id, **build_app_scope_filter(resolved_app_id)},
            {"$set": updates},
        )
        return bool(result.modified_count)

    async def create_change_request(
        self,
        *,
        app_id: str,
        build_family: str,
        build_key: str,
        build_record_id: str | None,
        raw_user_request: str,
        classification: ChangeClassification,
        refinement_request: dict[str, Any] | RefinementRequestPayload,
        change_intent: dict[str, Any] | ChangeIntentDoc,
        impact_set: dict[str, Any] | ImpactSetDoc,
        router_decision: dict[str, Any] | None = None,
        created_by_user_id: str | None = None,
    ) -> ChangeRequestDoc:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        refinement_request_doc = (
            refinement_request
            if isinstance(refinement_request, RefinementRequestPayload)
            else RefinementRequestPayload.model_validate(refinement_request)
        )
        change_intent_doc = (
            change_intent
            if isinstance(change_intent, ChangeIntentDoc)
            else ChangeIntentDoc.model_validate(change_intent)
        )
        impact_set_doc = (
            impact_set
            if isinstance(impact_set, ImpactSetDoc)
            else ImpactSetDoc.model_validate(impact_set)
        )
        doc = ChangeRequestDoc(
            _id=f"cr_{uuid4().hex[:24]}",
            app_id=resolved_app_id,
            build_family=build_family,
            build_key=build_key,
            build_record_id=build_record_id,
            raw_user_request=raw_user_request,
            classification=classification,
            refinement_request=refinement_request_doc,
            change_intent=change_intent_doc,
            impact_set=impact_set_doc,
            router_decision=dict(router_decision or {}),
            created_by_user_id=created_by_user_id,
        )
        coll = await self._coll("ChangeRequests")
        await coll.insert_one(doc.model_dump(by_alias=True, mode="python"))
        return doc

    async def list_change_requests(
        self,
        *,
        app_id: str,
        build_record_id: str | None = None,
        classification: ChangeClassification | None = None,
        limit: int = 50,
    ) -> list[ChangeRequestDoc]:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        query: dict[str, Any] = {"app_id": resolved_app_id}
        if build_record_id:
            query["build_record_id"] = build_record_id
        if classification:
            query["classification"] = classification.value
        coll = await self._coll("ChangeRequests")
        cursor = coll.find(query).sort("created_at", -1).limit(max(1, int(limit)))
        rows = await cursor.to_list(length=max(1, int(limit)))
        return [ChangeRequestDoc.model_validate(row) for row in rows]

    async def get_change_request(
        self,
        *,
        app_id: str,
        change_request_id: str,
    ) -> ChangeRequestDoc | None:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        coll = await self._coll("ChangeRequests")
        raw = await coll.find_one({"_id": change_request_id, **build_app_scope_filter(resolved_app_id)})
        if not isinstance(raw, dict):
            return None
        return ChangeRequestDoc.model_validate(raw)

    async def update_change_request_router_decision(
        self,
        *,
        app_id: str,
        change_request_id: str,
        router_decision: dict[str, Any],
    ) -> bool:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        coll = await self._coll("ChangeRequests")
        result = await coll.update_one(
            {"_id": change_request_id, **build_app_scope_filter(resolved_app_id)},
            {"$set": {"router_decision": dict(router_decision or {})}},
        )
        return bool(result.modified_count)

    async def set_artifact_lifecycle_status(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        lifecycle_status: ArtifactLifecycleStatus,
        invalidation_reason: str | None = None,
        invalidated_by_version_id: str | None = None,
    ) -> bool:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        updates: dict[str, Any] = {
            "lifecycle_status": lifecycle_status.value,
            "updated_at": _utc_now(),
        }
        if invalidation_reason is not None:
            updates["invalidation_reason"] = invalidation_reason
        if invalidated_by_version_id is not None:
            updates["invalidated_by_version_id"] = invalidated_by_version_id
        if lifecycle_status == ArtifactLifecycleStatus.STALE:
            updates["stale_at"] = _utc_now()

        versions = await self._coll("ArtifactVersions")
        result = await versions.update_one(
            {"_id": artifact_version_id, **build_app_scope_filter(resolved_app_id)},
            {"$set": updates},
        )
        return bool(result.modified_count)


    async def reject_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        reason: str,
    ) -> bool:
        return await self.set_artifact_lifecycle_status(
            app_id=app_id,
            artifact_version_id=artifact_version_id,
            lifecycle_status=ArtifactLifecycleStatus.ARCHIVED,
            invalidation_reason=reason,
        )

    async def create_refinement_session(
        self,
        *,
        app_id: str,
        build_record_id: str,
        change_request_id: str,
        result_build_record_id: str | None = None,
        provider: str = "e2b",
        sandbox_id: str | None = None,
        status: RefinementSessionStatus = RefinementSessionStatus.PROVISIONING,
        preview_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RefinementSessionDoc:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        doc = RefinementSessionDoc(
            _id=f"rs_{uuid4().hex[:24]}",
            app_id=resolved_app_id,
            build_record_id=build_record_id,
            result_build_record_id=result_build_record_id,
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
        status: RefinementSessionStatus | None = None,
        sandbox_id: str | None = None,
        result_build_record_id: str | None = None,
        preview_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        ended_at: datetime | None = None,
    ) -> bool:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        set_doc: dict[str, Any] = {"metadata": dict(metadata or {})} if metadata is not None else {}
        if status is not None:
            set_doc["status"] = status.value
        if sandbox_id is not None:
            set_doc["sandbox_id"] = sandbox_id
        if result_build_record_id is not None:
            set_doc["result_build_record_id"] = result_build_record_id
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

    async def list_refinement_sessions(
        self,
        *,
        app_id: str,
        build_record_id: str | None = None,
        result_build_record_id: str | None = None,
        change_request_id: str | None = None,
        limit: int = 20,
    ) -> list[RefinementSessionDoc]:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        query: dict[str, Any] = {"app_id": resolved_app_id}
        if build_record_id:
            query["build_record_id"] = build_record_id
        if result_build_record_id:
            query["result_build_record_id"] = result_build_record_id
        if change_request_id:
            query["change_request_id"] = change_request_id
        coll = await self._coll("RefinementSessions")
        cursor = coll.find(query).sort("started_at", -1).limit(max(1, int(limit)))
        rows = await cursor.to_list(length=max(1, int(limit)))
        return [RefinementSessionDoc.model_validate(row) for row in rows]

    async def get_current_build_record_refs(self, *, app_id: str) -> dict[str, str]:
        """Return a mapping of build_family → build_record_id for all CURRENT build records."""
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            return {}
        await self._ensure_client()
        versions = await self._coll("ArtifactVersions")
        try:
            scope = build_app_scope_filter(resolved_app_id)
            cursor = versions.find(
                {**scope, "lifecycle_status": ArtifactLifecycleStatus.CURRENT.value},
                projection={"build_family": 1, "_id": 1, "version_number": 1},
            ).sort("version_number", -1)
            rows = await cursor.to_list(length=200)
            refs: dict[str, str] = {}
            for row in rows:
                family = str(row.get("build_family") or "").strip()
                record_id = str(row.get("_id") or "").strip()
                if family and record_id and family not in refs:
                    refs[family] = record_id
            return refs
        except Exception as exc:
            logger.warning("get_current_build_record_refs failed for app %s: %s", resolved_app_id, exc)
            return {}

    async def get_stale_build_families(self, *, app_id: str) -> list[str]:
        """Return build family names that are genuinely stale (STALE but no CURRENT)."""
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            return []
        await self._ensure_client()
        versions = await self._coll("ArtifactVersions")
        try:
            scope = build_app_scope_filter(resolved_app_id)
            stale_families: set[str] = set(
                await versions.distinct(
                    "build_family",
                    {**scope, "lifecycle_status": ArtifactLifecycleStatus.STALE.value},
                )
            )
            if not stale_families:
                return []
            current_families: set[str] = set(
                await versions.distinct(
                    "build_family",
                    {**scope, "lifecycle_status": ArtifactLifecycleStatus.CURRENT.value},
                )
            )
            return sorted(str(k) for k in (stale_families - current_families) if k)
        except Exception as exc:
            logger.warning("get_stale_build_families failed for app %s: %s", resolved_app_id, exc)
            return []

    # ── New BuildRecord API methods ────────────────────────────────────────────

    async def create_build_record(
        self,
        *,
        app_id: str,
        build_family: str,
        build_key: str,
        files_manifest: Iterable[dict[str, Any] | ArtifactFileManifestEntry] | None = None,
        source_workflow: str | None = None,
        source_chat_id: str | None = None,
        parent_build_record_id: str | None = None,
        canonical_inputs_version: dict[str, str] | None = None,
        lifecycle_status: ArtifactLifecycleStatus = ArtifactLifecycleStatus.CURRENT,
        validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PENDING,
        commit_metadata: dict[str, Any] | ArtifactCommitMetadata | None = None,
        app_validation_status: str | None = None,
        app_validation_strategy: str | None = None,
        sandbox_session_id: str | None = None,
        sandbox_provider: str | None = None,
    ) -> BuildRecord:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")

        versions = await self._coll("ArtifactVersions")
        parent_doc: BuildRecord | None = None
        if parent_build_record_id:
            parent_raw = await versions.find_one(
                {"_id": parent_build_record_id, **build_app_scope_filter(resolved_app_id)}
            )
            if not isinstance(parent_raw, dict):
                raise ValueError(f"Unknown parent_build_record_id: {parent_build_record_id}")
            parent_doc = BuildRecord.model_validate(parent_raw)

        build_record_id = f"av_{uuid4().hex[:24]}"
        version_number = await self._next_build_record_version_number(
            app_id=resolved_app_id,
            build_family=build_family,
            build_key=build_key,
        )
        lineage_root_id = parent_doc.lineage_root_id if parent_doc else build_record_id
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
        record = BuildRecord(
            _id=build_record_id,
            app_id=resolved_app_id,
            build_family=build_family,
            build_key=build_key,
            version_number=version_number,
            parent_build_record_id=parent_build_record_id,
            lineage_root_id=lineage_root_id,
            source_workflow=source_workflow,
            source_chat_id=source_chat_id,
            canonical_inputs_version=dict(canonical_inputs_version or {}),
            lifecycle_status=lifecycle_status,
            validation_status=validation_status,
            app_validation_status=app_validation_status,
            app_validation_strategy=app_validation_strategy,
            sandbox_session_id=sandbox_session_id,
            sandbox_provider=sandbox_provider,
            files_manifest=manifest_entries,
            commit_metadata=commit_doc,
            created_at=now,
            updated_at=now,
        )

        if lifecycle_status == ArtifactLifecycleStatus.CURRENT:
            await versions.update_many(
                {
                    "app_id": resolved_app_id,
                    "build_family": build_family,
                    "build_key": build_key,
                    "lifecycle_status": ArtifactLifecycleStatus.CURRENT.value,
                },
                {
                    "$set": {
                        "lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED.value,
                        "updated_at": now,
                    }
                },
            )

        await versions.insert_one(record.model_dump(by_alias=True, mode="python"))
        return record

    async def get_build_record(
        self,
        *,
        app_id: str,
        build_record_id: str,
    ) -> BuildRecord | None:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        versions = await self._coll("ArtifactVersions")
        raw = await versions.find_one({"_id": build_record_id, **build_app_scope_filter(resolved_app_id)})
        if not isinstance(raw, dict):
            return None
        return BuildRecord.model_validate(raw)

    async def list_build_records(
        self,
        *,
        app_id: str,
        build_family: str | None = None,
        build_key: str | None = None,
        lineage_root_id: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 50,
    ) -> list[BuildRecord]:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        query: dict[str, Any] = {"app_id": resolved_app_id}
        if build_family:
            # Handle both build_family (new docs) and artifact_kind (prior docs)
            query["$or"] = [{"build_family": build_family}, {"artifact_kind": build_family}]
        if build_key:
            key_or: list[dict[str, Any]] = [{"build_key": build_key}, {"artifact_key": build_key}]
            if "$and" in query:
                query["$and"].append({"$or": key_or})  # type: ignore[attr-defined]
            else:
                query["$and"] = [{"$or": key_or}]
        if lineage_root_id:
            query["lineage_root_id"] = lineage_root_id
        if lifecycle_status is not None:
            query["lifecycle_status"] = lifecycle_status.value
        versions = await self._coll("ArtifactVersions")
        cursor = versions.find(query).sort([("version_number", -1), ("created_at", -1)]).limit(max(1, int(limit)))
        rows = await cursor.to_list(length=max(1, int(limit)))
        return [BuildRecord.model_validate(row) for row in rows]

    async def mark_build_record_stale(
        self,
        *,
        app_id: str,
        build_record_id: str,
        reason: str,
        invalidated_by_version_id: str | None = None,
    ) -> bool:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        versions = await self._coll("ArtifactVersions")
        now = _utc_now()
        result = await versions.update_one(
            {
                "_id": build_record_id,
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

    async def invalidate_build_family(
        self,
        *,
        app_id: str,
        build_family: str,
        build_key: str,
        reason: str,
        invalidated_by_version_id: str | None = None,
        exclude_version_id: str | None = None,
    ) -> int:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        query: dict[str, Any] = {
            "app_id": resolved_app_id,
            "build_family": build_family,
            "build_key": build_key,
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

    async def invalidate_build_record_refs(
        self,
        *,
        app_id: str,
        build_record_refs: dict[str, str],
        affected_build_families: Iterable[str] | None = None,
        reason: str,
        invalidated_by_version_id: str | None = None,
    ) -> list[str]:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")

        normalized_refs: dict[str, str] = {}
        for raw_family, raw_record_id in dict(build_record_refs or {}).items():
            family = str(raw_family or "").strip()
            record_id = str(raw_record_id or "").strip()
            if family and record_id:
                normalized_refs[family] = record_id

        target_families: list[str] = []
        raw_target_families = list(affected_build_families or normalized_refs.keys())
        for raw_family in raw_target_families:
            family = str(raw_family or "").strip()
            if family and family not in target_families:
                target_families.append(family)

        invalidated_ids: list[str] = []
        seen_record_ids: set[str] = set()
        for family in target_families:
            build_record_id = normalized_refs.get(family)
            if not build_record_id or build_record_id in seen_record_ids:
                continue
            seen_record_ids.add(build_record_id)
            if await self.mark_build_record_stale(
                app_id=resolved_app_id,
                build_record_id=build_record_id,
                reason=reason,
                invalidated_by_version_id=invalidated_by_version_id,
            ):
                invalidated_ids.append(build_record_id)
        return invalidated_ids

    async def accept_build_record(
        self,
        *,
        app_id: str,
        build_record_id: str,
        commit_metadata: dict[str, Any] | ArtifactCommitMetadata | None = None,
    ) -> BuildRecord | None:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        versions = await self._coll("ArtifactVersions")
        raw = await versions.find_one({"_id": build_record_id, **build_app_scope_filter(resolved_app_id)})
        if not isinstance(raw, dict):
            return None
        target = BuildRecord.model_validate(raw)
        now = _utc_now()
        updates: dict[str, Any] = {
            "lifecycle_status": ArtifactLifecycleStatus.CURRENT.value,
            "updated_at": now,
        }
        if commit_metadata is not None:
            commit_doc = (
                commit_metadata
                if isinstance(commit_metadata, ArtifactCommitMetadata)
                else ArtifactCommitMetadata.model_validate(commit_metadata)
            )
            updates["commit_metadata"] = commit_doc.model_dump(mode="python")

        await versions.update_many(
            {
                "app_id": resolved_app_id,
                "build_family": target.build_family,
                "build_key": target.build_key,
                "_id": {"$ne": target.id},
                "lifecycle_status": ArtifactLifecycleStatus.CURRENT.value,
            },
            {
                "$set": {
                    "lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED.value,
                    "updated_at": now,
                }
            },
        )
        await versions.update_one(
            {"_id": target.id, **build_app_scope_filter(resolved_app_id)},
            {"$set": updates},
        )
        refreshed = await versions.find_one({"_id": target.id, **build_app_scope_filter(resolved_app_id)})
        return BuildRecord.model_validate(refreshed) if isinstance(refreshed, dict) else target

    async def set_validation_status_for_build_record(
        self,
        *,
        app_id: str,
        build_record_id: str,
        validation_status: ArtifactValidationStatus,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        commit_metadata: dict[str, Any] | ArtifactCommitMetadata | None = None,
    ) -> bool:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        updates: dict[str, Any] = {
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
            {"_id": build_record_id, **build_app_scope_filter(resolved_app_id)},
            {"$set": updates},
        )
        return bool(result.modified_count)


_build_record_store: BuildRecordStore | None = None


def get_build_record_store() -> BuildRecordStore:
    global _build_record_store
    if _build_record_store is None:
        _build_record_store = BuildRecordStore()
    return _build_record_store


# Prior-api aliases — kept so callers that haven't been updated yet still work
ArtifactStore = BuildRecordStore
_artifact_store: BuildRecordStore | None = None


def get_artifact_store() -> BuildRecordStore:
    return get_build_record_store()


__all__ = [
    "BuildRecordStore",
    "get_build_record_store",
    # Prior-api aliases
    "ArtifactStore",
    "get_artifact_store",
]
