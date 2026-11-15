"""Friend requests are the single durable source of connection state."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]


def _collection(ctx, entity_name: str):
    if getattr(ctx, "persistence", None) is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return ctx.persistence.collection("friends", entity_name)


def _pair_id(first: str, second: str) -> str:
    encoded = json.dumps(sorted([first, second]), separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parties(user_id: str) -> dict[str, Any]:
    return {"$or": [{"requester_id": user_id}, {"recipient_id": user_id}]}


def friendship_view(request: Mapping[str, Any], user_id: str) -> Document:
    return {
        "friendship_id": f"fs_{request['request_id']}",
        "user_id": user_id,
        "friend_user_id": request["recipient_id"] if request["requester_id"] == user_id else request["requester_id"],
        "since": request["updated_at"],
        "created_at": request["updated_at"],
    }


class FriendRequestRepo:
    async def create(self, ctx, doc: Document) -> Document | None:
        collection = _collection(ctx, "requests")
        pair_id = _pair_id(doc["requester_id"], doc["recipient_id"])
        try:
            await collection.update_one(
                {"_id": pair_id, "status": {"$nin": ["pending", "accepted"]}},
                {"$set": dict(doc), "$unset": {"acceptance_event_id": "", "accepted_at": ""}}, upsert=True,
            )
        except Exception:
            # A simultaneous request or an uncertain write is reconciled by the
            # stable pair identity and this attempt's fresh request identity.
            current = await collection.find_one({"_id": pair_id})
            if current and current.get("request_id") == doc["request_id"]:
                return doc
            if current and current.get("status") in {"pending", "accepted"}:
                return None
            raise
        return doc

    async def get(self, ctx, *, request_id: str) -> Document | None:
        row = await _collection(ctx, "requests").find_one({"request_id": request_id})
        return dict(row) if isinstance(row, Mapping) else None

    async def find_pending(self, ctx, *, requester_id: str, recipient_id: str) -> Document | None:
        row = await _collection(ctx, "requests").find_one({"requester_id": requester_id, "recipient_id": recipient_id, "status": "pending"})
        return dict(row) if isinstance(row, Mapping) else None

    async def update_status(self, ctx, *, request_id: str, status: str, updated_at: str, event_id: str | None = None) -> bool:
        changes = {"status": status, "updated_at": updated_at}
        if status == "accepted":
            if not event_id:
                raise ValueError("Acceptance requires a durable event identity")
            changes.update(acceptance_event_id=event_id, accepted_at=updated_at)
        result = await _collection(ctx, "requests").update_one(
            {"request_id": request_id, "status": "pending"},
            {"$set": changes},
        )
        return bool(getattr(result, "modified_count", 0))

    async def list(self, ctx, *, query: dict[str, Any], limit: int, before: str | None = None) -> list[Document]:
        if before:
            query = {**query, "created_at": {"$lt": before}}
        rows = await _collection(ctx, "requests").find_many(query, limit=limit, sort=[("created_at", -1)])
        return [dict(row) for row in rows or [] if isinstance(row, Mapping)]

    async def count(self, ctx, *, query: dict[str, Any]) -> int:
        return int(await _collection(ctx, "requests").count(query))


class FriendshipRepo:
    async def find(self, ctx, *, user_id: str, friend_user_id: str) -> Document | None:
        row = await _collection(ctx, "requests").find_one({"_id": _pair_id(user_id, friend_user_id), "status": "accepted"})
        return friendship_view(row, user_id) if row else None

    async def remove(self, ctx, *, user_id: str, friend_user_id: str) -> int:
        from .schemas import timestamp_now

        result = await _collection(ctx, "requests").update_one(
            {"_id": _pair_id(user_id, friend_user_id), "status": "accepted"},
            {"$set": {"status": "removed", "updated_at": timestamp_now()}},
        )
        return int(getattr(result, "modified_count", 0))

    async def list(self, ctx, *, user_id: str, limit: int, before: str | None = None) -> list[Document]:
        query = {"status": "accepted", **_parties(user_id)}
        if before:
            query["updated_at"] = {"$lt": before}
        rows = await _collection(ctx, "requests").find_many(query, limit=limit, sort=[("updated_at", -1)])
        return [friendship_view(row, user_id) for row in rows or []]

    async def count(self, ctx, *, user_id: str) -> int:
        return int(await _collection(ctx, "requests").count({"status": "accepted", **_parties(user_id)}))
