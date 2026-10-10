"""Mongo authority for bounded preview admission and fenced session mutations.

Admission uses one bounded document so local standalone Mongo has the same
atomic capacity guarantees as a replica set. Provider calls never run inside
the compare-and-swap loop. Reservations remain until confirmed cleanup or the
conservative provider deadline; operation-lease expiry does not release them.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast
from uuid import uuid4

from bson import BSON
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, PlatformCollections
from mozaiksai.core.sandbox.sealed_runtime_ref import is_sealed_runtime_ref

_LEDGER_ID = "artifact-previews"
_IDENTITY_FIELDS = ("app_id", "user_id", "artifact_id", "target_app_id", "build_registry_id", "provider")
_SEALED_IDENTITY_FIELDS = ("sealed_archive_sha256", "sealed_runtime_ref")
_STATE_FIELDS = {
    "session_id", "status", "preview_url", "last_error", "last_access_at",
    "manifest", "paths", "has_requirements", "health_checked_at",
}
_MAX_RESERVATIONS = 4096
_T = TypeVar("_T")


class PreviewCapacityError(RuntimeError):
    """The shared preview admission limit has been reached."""


class PreviewLeaseLostError(RuntimeError):
    """A stale operation cannot mutate or release a preview."""


class PreviewOperationBusy(RuntimeError):
    """Another worker holds the preview operation lease."""


class PreviewRecoveryRequired(RuntimeError):
    """An interrupted mutation requires termination before another mutation."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MongoPreviewStore:
    def __init__(self, db: Any = None, *, now: Callable[[], datetime] | None = None) -> None:
        self._db = db
        self._now = now or (lambda: datetime.now(UTC))

    def _database(self) -> Any:
        if self._db is not None:
            return self._db
        from mozaiksai.core.core_config import get_mongo_client

        client = get_mongo_client()
        if client is None:
            raise RuntimeError("MongoDB is required for preview coordination")
        return client[SYSTEM_DATABASE]

    def _ledger_collection(self) -> Any:
        return self._database()[PlatformCollections.PREVIEW_COORDINATION]

    def _sessions_collection(self) -> Any:
        return self._database()[PlatformCollections.PREVIEW_SESSIONS]

    async def _ledger(self) -> dict[str, Any]:
        collection = self._ledger_collection()
        document = await collection.find_one({"_id": _LEDGER_ID})
        if document is None:
            document = {"_id": _LEDGER_ID, "revision": 0, "limits": {}, "entries": []}
            try:
                await collection.insert_one(document)
            except DuplicateKeyError:
                document = await collection.find_one({"_id": _LEDGER_ID})
        return cast(dict[str, Any], document)

    async def _change(self, update: Callable[[dict[str, Any]], _T]) -> _T:
        for attempt in range(100):
            document = await self._ledger()
            replacement = deepcopy(document)
            result = update(replacement)
            if replacement == document:
                return deepcopy(result)
            replacement["revision"] = document["revision"] + 1
            written = await self._ledger_collection().replace_one(
                {"_id": _LEDGER_ID, "revision": document["revision"]}, replacement,
            )
            if written.matched_count:
                return deepcopy(result)
            await asyncio.sleep(min(attempt * 0.001, 0.025))
        raise PreviewOperationBusy("Preview admission is busy; retry shortly")

    def _prune_queued(self, document: dict[str, Any]) -> None:
        now = self._now()
        document["entries"] = [entry for entry in document["entries"]
                               if entry["phase"] != "queued" or _utc(entry["queue_deadline"]) > now]

    @staticmethod
    def _bind_limits(document: dict[str, Any], limits: dict[str, int]) -> None:
        if not document["entries"]:
            document["limits"] = {}
        for name, value in limits.items():
            if value <= 0 or value > _MAX_RESERVATIONS:
                raise ValueError(f"{name} must be between 1 and {_MAX_RESERVATIONS}")
            existing = document["limits"].get(name)
            if existing is not None and existing != value:
                raise ValueError("Preview capacity configuration differs between workers")
            document["limits"][name] = value

    async def reserve(
        self, identity: dict[str, Any], *, max_sessions: int, max_owner_sessions: int,
        max_pending: int, queue_seconds: float, ttl_seconds: float,
    ) -> dict[str, Any]:
        fields = set(identity)
        if fields not in (set(_IDENTITY_FIELDS), set(_IDENTITY_FIELDS) | set(_SEALED_IDENTITY_FIELDS)) or not all(
            isinstance(value, str) and value for value in identity.values()
        ):
            raise ValueError("Preview requires its complete immutable identity")
        if fields & set(_SEALED_IDENTITY_FIELDS):
            if (
                re.fullmatch(r"sha256:[0-9a-f]{64}", identity["sealed_archive_sha256"]) is None
                or not is_sealed_runtime_ref(identity["provider"], identity["sealed_runtime_ref"])
            ):
                raise ValueError("Sealed preview requires an exact archive digest and provider runtime reference")
        if queue_seconds <= 0 or ttl_seconds <= 0 or max_sessions + max_pending > _MAX_RESERVATIONS:
            raise ValueError("Preview queue and lifetime must be positive and bounded")
        now = self._now()
        entry = {
            **identity, "sandbox_id": uuid4().hex, "phase": "queued",
            "created_at": now, "queue_deadline": now + timedelta(seconds=queue_seconds),
            "expires_at": now + timedelta(seconds=ttl_seconds), "allocation_token": None,
        }

        def update(document: dict[str, Any]) -> dict[str, Any]:
            self._prune_queued(document)
            self._bind_limits(document, {"max_sessions": max_sessions, "max_owner_sessions": max_owner_sessions, "max_pending": max_pending})
            for current in document["entries"]:
                if all(current[name] == identity[name] for name in ("app_id", "user_id", "artifact_id")):
                    if any(current.get(name) != identity.get(name) for name in (*_IDENTITY_FIELDS, *_SEALED_IDENTITY_FIELDS)):
                        raise ValueError("Preview artifact identity changed")
                    return cast(dict[str, Any], current)
            if sum(item["phase"] == "queued" for item in document["entries"]) >= max_pending:
                raise PreviewCapacityError("Preview startup queue is full; try again later")
            if len(document["entries"]) >= _MAX_RESERVATIONS:
                raise PreviewCapacityError("Preview capacity reached; try again later")
            document["entries"].append(entry)
            return entry

        return await self._change(update)

    async def try_allocate(
        self, sandbox_id: str, *, max_parallel_creates: int, provider_deadline: datetime,
    ) -> dict[str, Any] | None:
        if _utc(provider_deadline) <= self._now():
            raise ValueError("Provider deadline must be in the future")

        def update(document: dict[str, Any]) -> dict[str, Any] | None:
            self._bind_limits(document, {"max_parallel_creates": max_parallel_creates})
            self._prune_queued(document)
            entry = next((item for item in document["entries"] if item["sandbox_id"] == sandbox_id), None)
            if entry is None:
                raise KeyError("Preview reservation expired")
            if entry["phase"] != "queued":
                return None
            active = [item for item in document["entries"] if item["phase"] != "queued"]
            limits = document["limits"]
            if len(active) >= limits["max_sessions"] or sum(item["phase"] == "provisioning" for item in active) >= max_parallel_creates:
                return None
            for candidate in document["entries"]:
                if candidate["phase"] != "queued":
                    continue
                owner = (candidate["app_id"], candidate["user_id"])
                if sum((item["app_id"], item["user_id"]) == owner for item in active) >= limits["max_owner_sessions"]:
                    continue
                if candidate["sandbox_id"] != sandbox_id:
                    return None
                candidate.update(phase="provisioning", allocation_token=uuid4().hex, expires_at=_utc(provider_deadline))
                return cast(dict[str, Any], candidate)
            return None

        return await self._change(update)

    async def abandon_queued(self, sandbox_id: str) -> bool:
        def update(document: dict[str, Any]) -> bool:
            prior = len(document["entries"])
            document["entries"] = [item for item in document["entries"] if not (item["sandbox_id"] == sandbox_id and item["phase"] == "queued")]
            return len(document["entries"]) != prior

        return await self._change(update)

    @staticmethod
    def validate_state(payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - _STATE_FIELDS:
            raise ValueError("Unsupported preview state field")
        if "manifest" in payload and payload["manifest"] is not None and not isinstance(payload["manifest"], str):
            raise ValueError("Preview manifest must be text")
        if "paths" in payload and (not isinstance(payload["paths"], list) or not all(isinstance(path, str) for path in payload["paths"])):
            raise ValueError("Preview paths must be a list of strings")
        if "has_requirements" in payload and not isinstance(payload["has_requirements"], bool):
            raise ValueError("Preview requirements marker must be boolean")
        if len(BSON.encode(payload)) > 1024 * 1024:
            raise ValueError("Preview metadata exceeds its storage limit")
        return deepcopy(payload)

    async def attach_session(
        self, sandbox_id: str, allocation_token: str, state: dict[str, Any], *, expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        state = self.validate_state(state)
        if not isinstance(state.get("session_id"), str) or not state["session_id"]:
            raise ValueError("Provider session id is required")
        entry = await self.get(sandbox_id)
        if entry is None or entry["allocation_token"] != allocation_token or entry["phase"] not in {"provisioning", "active"}:
            raise PreviewLeaseLostError("Preview allocation is no longer owned")
        deadline = _utc(expires_at) if expires_at is not None else entry["expires_at"]
        if deadline > entry["expires_at"]:
            raise ValueError("Confirmed provider deadline cannot extend its reservation")
        document = {
            "_id": sandbox_id, "allocation_token": allocation_token, "revision": 0,
            "state": state, "operation": None, "updated_at": self._now(), "provider_expires_at": deadline,
        }
        try:
            await self._sessions_collection().insert_one(document)
        except DuplicateKeyError:
            current = await self._sessions_collection().find_one({"_id": sandbox_id})
            if current["allocation_token"] != allocation_token or current["state"].get("session_id") != state["session_id"]:
                raise PreviewLeaseLostError("Provider session attachment changed") from None

        def update(ledger: dict[str, Any]) -> None:
            for current in ledger["entries"]:
                if current["sandbox_id"] == sandbox_id and current["allocation_token"] == allocation_token:
                    current["phase"] = "active"
                    current["expires_at"] = deadline
                    return
            raise PreviewLeaseLostError("Preview allocation is no longer owned")

        await self._change(update)
        return await self.get(sandbox_id)  # type: ignore[return-value]

    def _snapshot(self, entry: dict[str, Any], session: dict[str, Any] | None) -> dict[str, Any]:
        result = {**entry, "status": "starting", "session_id": None, "preview_url": None, "last_error": None,
                  "manifest": None, "paths": [], "has_requirements": False, "health_checked_at": None,
                  "operation": None, "revision": 0}
        if session is not None:
            result.update(session["state"], operation=session["operation"], revision=session["revision"])
            if session["state"].get("session_id"):
                result["phase"] = "active"
                result["expires_at"] = session.get("provider_expires_at", result["expires_at"])
        for field in ("created_at", "queue_deadline", "expires_at", "last_access_at", "health_checked_at"):
            if isinstance(result.get(field), datetime):
                result[field] = _utc(result[field])
        if result["operation"]:
            result["operation"] = {**result["operation"], "expires_at": _utc(result["operation"]["expires_at"])}
            if result["operation"]["expires_at"] <= self._now():
                result.update(status="error", preview_url=None, last_error="Preview operation interrupted; recreate the preview")
        return deepcopy(result)

    async def _recover_receipts(self, entries: list[dict[str, Any]], sessions: dict[str, Any]) -> None:
        receipts = {entry["sandbox_id"]: sessions[entry["sandbox_id"]] for entry in entries
                    if entry["phase"] == "provisioning" and entry["sandbox_id"] in sessions}
        if not receipts:
            return

        def update(ledger: dict[str, Any]) -> None:
            for entry in ledger["entries"]:
                receipt = receipts.get(entry["sandbox_id"])
                if receipt and receipt["allocation_token"] == entry["allocation_token"]:
                    entry.update(phase="active", expires_at=receipt.get("provider_expires_at", entry["expires_at"]))

        await self._change(update)

    async def get(self, sandbox_id: str) -> dict[str, Any] | None:
        document = await self._ledger()
        entry = next((item for item in document["entries"] if item["sandbox_id"] == sandbox_id), None)
        if entry is None:
            return None
        session = await self._sessions_collection().find_one({"_id": sandbox_id})
        if session is not None:
            await self._recover_receipts([entry], {sandbox_id: session})
        return self._snapshot(entry, session)

    async def list(self) -> list[dict[str, Any]]:
        document = await self._ledger()
        ids = [entry["sandbox_id"] for entry in document["entries"]]
        sessions = await self._sessions_collection().find({"_id": {"$in": ids}}).to_list(length=_MAX_RESERVATIONS)
        by_id = {session["_id"]: session for session in sessions}
        await self._recover_receipts(document["entries"], by_id)
        return [self._snapshot(entry, by_id.get(entry["sandbox_id"])) for entry in document["entries"]]

    async def claim_operation(self, sandbox_id: str, *, kind: str, lease_seconds: float) -> str:
        if kind not in {"sync", "start", "status", "stop", "recovery"} or lease_seconds <= 0:
            raise ValueError("Invalid preview operation lease")
        for _ in range(25):
            current = await self._sessions_collection().find_one({"_id": sandbox_id})
            if current is None:
                raise KeyError("Preview session not found")
            now = self._now()
            operation = current["operation"]
            if current.get("closing") and kind not in {"stop", "recovery"}:
                raise PreviewRecoveryRequired("Preview cleanup is in progress")
            if operation:
                if _utc(operation["expires_at"]) > now:
                    raise PreviewOperationBusy("Preview operation is already in progress")
                if kind not in {"stop", "recovery"}:
                    raise PreviewRecoveryRequired("Interrupted preview operation requires cleanup")
            token = uuid4().hex
            replacement = deepcopy(current)
            replacement.update(revision=current["revision"] + 1, updated_at=now,
                               operation={"token": token, "kind": kind, "expires_at": now + timedelta(seconds=lease_seconds)})
            if kind in {"stop", "recovery"}:
                # A failed admission-ledger write can leave closing set. Only
                # a fresh cleanup owner may retry its receipt and release.
                replacement["closing"] = False
            if operation:
                replacement["state"].update(status="error", preview_url=None, last_error="Preview operation interrupted; recreate the preview")
            result = await self._sessions_collection().replace_one({"_id": sandbox_id, "revision": current["revision"]}, replacement)
            if result.matched_count:
                return token
        raise PreviewOperationBusy("Preview operation is busy")

    async def renew_operation(self, sandbox_id: str, token: str, lease_seconds: float) -> None:
        if lease_seconds <= 0:
            raise ValueError("Preview operation lease must be positive")
        now = self._now()
        result = await self._sessions_collection().update_one(
            {"_id": sandbox_id, "operation.token": token, "operation.expires_at": {"$gt": now}},
            {"$set": {"operation.expires_at": now + timedelta(seconds=lease_seconds), "updated_at": now}, "$inc": {"revision": 1}},
        )
        if not result.matched_count:
            raise PreviewLeaseLostError("Preview operation lease was lost")

    async def save(self, sandbox_id: str, state: dict[str, Any], operation_token: str) -> dict[str, Any]:
        payload = self.validate_state(state)
        now = self._now()
        result = await self._sessions_collection().update_one(
            {"_id": sandbox_id, "operation.token": operation_token, "operation.expires_at": {"$gt": now}, "closing": {"$ne": True}},
            {"$set": {**{f"state.{key}": value for key, value in payload.items()}, "updated_at": now}, "$inc": {"revision": 1}},
        )
        if not result.matched_count:
            raise PreviewLeaseLostError("Preview operation lease was lost")
        current = await self.get(sandbox_id)
        if current is None:
            raise PreviewLeaseLostError("Preview reservation was released")
        return current

    async def release_operation(self, sandbox_id: str, token: str) -> bool:
        result = await self._sessions_collection().update_one(
            {"_id": sandbox_id, "operation.token": token, "operation.expires_at": {"$gt": self._now()}},
            {"$set": {"operation": None, "updated_at": self._now()}, "$inc": {"revision": 1}},
        )
        return bool(result.matched_count)

    async def release(
        self, sandbox_id: str, *, allocation_token: str | None = None,
        operation_token: str | None = None, provider_absent: bool = False,
    ) -> bool:
        session = await self._sessions_collection().find_one({"_id": sandbox_id})
        if session is not None:
            operation = session["operation"]
            if not operation or operation["token"] != operation_token or _utc(operation["expires_at"]) <= self._now():
                raise PreviewLeaseLostError("Preview cleanup requires its current operation lease")
            if operation["kind"] not in {"stop", "recovery"}:
                raise PreviewLeaseLostError("Only a cleanup operation can release a preview")
            closed = await self._sessions_collection().update_one(
                {"_id": sandbox_id, "operation.token": operation_token, "operation.expires_at": {"$gt": self._now()}},
                {"$set": {"closing": True}, "$inc": {"revision": 1}},
            )
            if not closed.matched_count:
                raise PreviewLeaseLostError("Preview cleanup lease was lost")

        def update(document: dict[str, Any]) -> bool:
            entry = next((item for item in document["entries"] if item["sandbox_id"] == sandbox_id), None)
            if entry is None:
                return False
            if entry["phase"] != "queued":
                if session is None and entry["allocation_token"] != allocation_token:
                    raise PreviewLeaseLostError("Preview cleanup requires its allocation token")
                if not provider_absent and _utc(entry["expires_at"]) > self._now():
                    raise PreviewRecoveryRequired("Provider cleanup is not yet confirmed")
            document["entries"] = [item for item in document["entries"] if item["sandbox_id"] != sandbox_id]
            return True

        removed = await self._change(update)
        if removed:
            # A stale release cannot remove a successor's record: sandbox IDs
            # are never reused, and all capacity authority is in the ledger.
            await self._sessions_collection().delete_one({"_id": sandbox_id})
        return removed
