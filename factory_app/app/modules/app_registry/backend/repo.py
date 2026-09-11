from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.multitenant import build_app_scope_filter

from .policy import owner_filter

IndexSpec = tuple[Sequence[tuple[str, int]], dict[str, Any]]
APP_REGISTRY_COLLECTION = "AppRegistryRecords"
BUILD_CONTINUE_STATES = frozenset({"draft", "building", "review", "configuring", "needs_revision"})
BUILD_RUN_HISTORY_LIMIT = 25
BUILD_RUN_COMPLETE_STATES = frozenset({"review", "active", "archived"})


class AppRegistryRepo:
    def __init__(self, pm: AG2PersistenceManager | None = None) -> None:
        self._pm = pm or AG2PersistenceManager()

    async def _client(self):
        await self._pm.persistence._ensure_client()  # noqa: SLF001
        client = self._pm.persistence.client
        if client is None:
            raise RuntimeError("Mongo client not initialized")
        return client

    async def _collection(self):
        client = await self._client()
        return client[SYSTEM_DATABASE][APP_REGISTRY_COLLECTION]

    async def get_owned_chat_binding(
        self, *, app_id: str, owner_user_id: str, chat_id: str
    ) -> dict[str, Any] | None:
        coll = await self._pm._coll()
        doc = await coll.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id), "user_id": owner_user_id},
            {"run_build_binding": 1},
        )
        return (doc.get("run_build_binding") or {}) if isinstance(doc, dict) else None

    async def ensure_indexes(self) -> None:
        coll = await self._collection()
        try:
            existing = await coll.list_indexes().to_list(length=None)
            names = {item.get("name") for item in existing if isinstance(item, dict)}
        except Exception:
            names = set()

        specs: Iterable[IndexSpec] = [
            ((("app_id", 1),), {"unique": True, "name": "app_registry_app_id_unique"}),
            ((("owner_user_id", 1), ("updated_at", -1)), {"name": "app_registry_owner_updated"}),
            ((("lifecycle_state", 1), ("updated_at", -1)), {"name": "app_registry_state_updated"}),
        ]
        for keys, kwargs in specs:
            name = kwargs.get("name")
            if name and name in names:
                continue
            await coll.create_index(list(keys), **kwargs)

    async def upsert_app_record(
        self,
        *,
        owner_user_id: str,
        name: str | None,
        description: str | None,
        lifecycle_state: str,
        app_id: str,
        chat_app_id: str | None = None,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
        name_source: str = "provisional",
        name_status: str = "provisional",
        build_context_profile: dict[str, Any] | None = None,
        current_build_run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = {"app_id": app_id, **owner_filter(owner_user_id)}
        await self.ensure_indexes()
        coll = await self._collection()
        now = datetime.now(UTC)
        # Claim immutable ownership before merging lifecycle metadata. A failed
        # merge leaves an owned draft that the same owner can safely reopen.
        try:
            existing = await coll.find_one_and_update(
                query,
                {"$setOnInsert": {
                    "_id": f"appreg_{uuid4().hex}", "created_at": now,
                    "updated_at": now, "lifecycle_state": "draft",
                    "bundle_path": None, "description": None,
                }},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            existing = await coll.find_one(query)
            if existing is None:
                raise ValueError("App id is not available") from exc
        if existing is None:
            raise RuntimeError("App record ownership could not be established")
        build_registry_id = str(existing["_id"])
        existing_name_status = str((existing or {}).get("name_status") or "").strip()
        incoming_named = name_status == "named" and bool(name)
        set_fields: dict[str, Any] = {
            "lifecycle_state": lifecycle_state,
            "updated_at": now,
            "last_status_changed_at": now,
        }
        if incoming_named:
            set_fields["name"] = name
            set_fields["name_status"] = "named"
            set_fields["name_source"] = name_source
            set_fields["named_at"] = (existing or {}).get("named_at") or now
            if description is not None:
                set_fields["description"] = description
        elif existing_name_status != "named":
            set_fields["name"] = name
            set_fields["name_status"] = "provisional"
            set_fields["name_source"] = "provisional"
            if description is not None:
                set_fields["description"] = description
        if active_chat_id:
            set_fields["active_chat_id"] = active_chat_id
        if active_workflow_id:
            set_fields["active_workflow_id"] = active_workflow_id
        if chat_app_id:
            set_fields["chat_app_id"] = chat_app_id
        if build_context_profile:
            set_fields["build_context_profile"] = self._merge_build_context_profile(
                existing=(existing or {}).get("build_context_profile"),
                incoming=build_context_profile,
                now=now,
            )
        should_track_build_run = bool(current_build_run or existing.get("current_build_run"))
        if should_track_build_run:
            build_run = self._merge_build_run(
                existing_run=(existing or {}).get("current_build_run"),
                incoming=current_build_run,
                lifecycle_state=lifecycle_state,
                active_chat_id=active_chat_id,
                active_workflow_id=active_workflow_id,
                now=now,
            )
            set_fields["current_build_run"] = build_run
            set_fields["build_runs"] = self._upsert_build_run(
                (existing or {}).get("build_runs"),
                build_run,
            )
        doc = await coll.find_one_and_update(
            {"_id": build_registry_id, **owner_filter(owner_user_id)},
            {"$set": set_fields},
            return_document=ReturnDocument.AFTER,
        )
        normalized = self._normalize_doc(doc)
        if normalized is None:
            raise RuntimeError("App record could not be loaded after upsert")
        return normalized

    async def update_lifecycle_state(
        self,
        *,
        build_registry_id: str,
        owner_user_id: str,
        lifecycle_state: str,
        bundle_path: str | None = None,
        artifact_version_id: str | None = None,
        workflow_sequence: str | None = None,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
        current_build_run: dict[str, Any] | None = None,
        expected_build_id: str | None = None,
        expected_artifact_version_id: str | None = None,
        expected_lifecycle_state: str | None = None,
    ) -> dict[str, Any] | None:
        query = {"_id": build_registry_id, **owner_filter(owner_user_id)}
        if expected_build_id is not None:
            query["current_build_run.build_id"] = expected_build_id
        if expected_artifact_version_id is not None:
            query["current_build_run.artifact_version_id"] = expected_artifact_version_id
        if expected_lifecycle_state is not None:
            query["lifecycle_state"] = expected_lifecycle_state
        await self.ensure_indexes()
        coll = await self._collection()
        existing = await coll.find_one(query)
        if not existing:
            return None
        query["updated_at"] = existing.get("updated_at")
        now = datetime.now(UTC)
        update_fields: dict[str, Any] = {
            "lifecycle_state": lifecycle_state,
            "updated_at": now,
            "last_status_changed_at": now,
        }
        if bundle_path is not None:
            update_fields["bundle_path"] = bundle_path
        if active_chat_id:
            update_fields["active_chat_id"] = active_chat_id
        if active_workflow_id:
            update_fields["active_workflow_id"] = active_workflow_id
        should_track_build_run = bool(current_build_run or existing.get("current_build_run"))
        if should_track_build_run:
            build_run = self._merge_build_run(
                existing_run=existing.get("current_build_run"),
                incoming=current_build_run,
                lifecycle_state=lifecycle_state,
                workflow_sequence=workflow_sequence,
                active_chat_id=active_chat_id,
                active_workflow_id=active_workflow_id,
                artifact_version_id=artifact_version_id,
                bundle_path=bundle_path,
                now=now,
            )
            update_fields["current_build_run"] = build_run
            update_fields["build_runs"] = self._upsert_build_run(existing.get("build_runs"), build_run)
            if build_run["build_id"] != (existing.get("current_build_run") or {}).get("build_id"):
                update_fields["active_chat_id"] = build_run.get("active_chat_id")
                update_fields["active_workflow_id"] = build_run.get("active_workflow_id")
        doc = await coll.find_one_and_update(query, {"$set": update_fields}, return_document=ReturnDocument.AFTER)
        return self._normalize_doc(doc)

    async def list_apps_for_user(self, *, owner_user_id: str) -> list[dict[str, Any]]:
        query = owner_filter(owner_user_id)
        await self.ensure_indexes()
        coll = await self._collection()
        docs = await coll.find(query).sort("updated_at", -1).to_list(length=500)
        return [normalized for doc in docs if (normalized := self._normalize_doc(doc))]

    async def get_by_app_id(self, *, app_id: str, owner_user_id: str) -> dict[str, Any] | None:
        query = {"app_id": app_id, **owner_filter(owner_user_id)}
        await self.ensure_indexes()
        coll = await self._collection()
        doc = await coll.find_one(query)
        return self._normalize_doc(doc)

    async def get_by_build_registry_id(self, *, build_registry_id: str, owner_user_id: str) -> dict[str, Any] | None:
        query = {"_id": build_registry_id, **owner_filter(owner_user_id)}
        await self.ensure_indexes()
        coll = await self._collection()
        doc = await coll.find_one(query)
        return self._normalize_doc(doc)

    async def delete_app(self, *, build_registry_id: str, owner_user_id: str) -> bool:
        query = {"_id": build_registry_id, **owner_filter(owner_user_id)}
        coll = await self._collection()
        result = await coll.delete_one(query)
        return int(getattr(result, "deleted_count", 0) or 0) > 0

    @staticmethod
    def _normalize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(doc, dict):
            return None
        normalized = cast(dict[str, Any], AppRegistryRepo._serialize_value(dict(doc)))
        normalized["build_registry_id"] = str(normalized.pop("_id"))
        return normalized

    @staticmethod
    def _merge_build_context_profile(
        *,
        existing: Any,
        incoming: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        profile = dict(existing) if isinstance(existing, dict) else {}
        profile.update(incoming)
        profile.setdefault("source", "factory_app")
        profile.setdefault("workflow_sequence", "build")
        profile.setdefault("captured_at", now)
        profile["updated_at"] = now
        return profile

    @staticmethod
    def _merge_build_run(
        *,
        existing_run: Any,
        incoming: dict[str, Any] | None,
        lifecycle_state: str,
        now: datetime,
        workflow_sequence: str | None = None,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
        artifact_version_id: str | None = None,
        bundle_path: str | None = None,
    ) -> dict[str, Any]:
        existing = dict(existing_run) if isinstance(existing_run, dict) else {}
        source = dict(incoming) if isinstance(incoming, dict) else {}
        build_id = source.get("build_id") or existing.get("build_id")
        if not build_id:
            raise ValueError("A build run requires its server-assigned build_id")
        if existing.get("build_id") != build_id:
            existing = {}

        def choose(*values: Any) -> str | None:
            for value in values:
                text = str(value or "").strip()
                if text:
                    return text
            return None

        run: dict[str, Any] = {
            "build_id": build_id,
            "phase": choose(source.get("phase"), existing.get("phase")),
            "workflow_sequence": choose(
                source.get("workflow_sequence"),
                workflow_sequence,
                existing.get("workflow_sequence"),
                "build",
            ),
            "status": lifecycle_state,
            "active_chat_id": choose(source.get("active_chat_id"), active_chat_id, existing.get("active_chat_id")),
            "active_workflow_id": choose(
                source.get("active_workflow_id"),
                active_workflow_id,
                existing.get("active_workflow_id"),
            ),
            "artifact_version_id": choose(
                source.get("artifact_version_id"),
                artifact_version_id,
                existing.get("artifact_version_id"),
            ),
            "bundle_path": choose(source.get("bundle_path"), bundle_path, existing.get("bundle_path")),
            "started_at": existing.get("started_at") or source.get("started_at") or now,
            "updated_at": now,
        }
        if lifecycle_state in BUILD_RUN_COMPLETE_STATES:
            run["completed_at"] = existing.get("completed_at") or source.get("completed_at") or now
        elif existing.get("completed_at"):
            run["completed_at"] = existing["completed_at"]
        return {key: value for key, value in run.items() if value is not None}

    @staticmethod
    def _upsert_build_run(existing_runs: Any, current_run: dict[str, Any]) -> list[dict[str, Any]]:
        build_id = str(current_run.get("build_id") or "").strip()
        runs = [dict(item) for item in existing_runs if isinstance(item, dict)] if isinstance(existing_runs, list) else []
        replaced = False
        for index, run in enumerate(runs):
            if build_id and str(run.get("build_id") or "").strip() == build_id:
                runs[index] = current_run
                replaced = True
                break
        if not replaced:
            runs.append(current_run)
        return runs[-BUILD_RUN_HISTORY_LIMIT:]

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: AppRegistryRepo._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [AppRegistryRepo._serialize_value(item) for item in value]
        return value
