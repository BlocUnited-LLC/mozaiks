"""Export and delete account contributions while preserving peer conversations.

The AppGenerator messaging archetype requires deletion to remove or anonymise the
user's message content, DMs, and thread participation records, and export to
return sent messages and thread membership.

Deletion removes the user's messages and read state, and the threads the user
created in which no other participant has posted. A surviving thread whose
last_message preview copies the user's text is repointed at its newest
remaining message, or cleared when none remains. The user leaves participant_ids
of every surviving thread, so participant checks admit only the remaining
participants; a send never writes back the participant list it read, so one
that overlaps the deletion cannot restore the user. created_by is a required
string and becomes a blank marker that no actor id equals, and a non-blank
title the user gave a surviving thread (a support thread is titled with its
opening message) becomes a neutral title; the creator's user_id scope stamp is
removed. In-app
notifications rendered from message_sent since pack 0.1.14 hold only thread and
message references, so deleted messages leave them nothing to show. Those
rendered by an earlier pack keep the body_preview they copied: they live in the
platform notification store, which the account protocol does not give module
handlers.

Export returns sent messages, read state, and one fixed thread projection, without
peers' previews or participant ids, for the threads the user created and for
every thread the user participates in.
"""
from __future__ import annotations

from typing import Any

_PAGE_SIZE = 100
# schemas.MESSAGE_PREVIEW_LENGTH; the handler stays free of package imports.
_PREVIEW_LENGTH = 160
# policy.actor_id never yields a blank id, and blank account ids are refused.
_RELEASED_CREATOR = ""
_RELEASED_TITLE = "Conversation"
_THREAD_FIELDS = (
    "thread_id", "thread_type", "title", "status", "scope_type", "scope_id", "subject_app_id",
    "related_type", "related_id", "created_at",
)
# Thread annotations are supplied by the thread's creator, so they belong in the
# creator's export and never in another participant's membership rows.
_CREATED_THREAD_FIELDS = (*_THREAD_FIELDS, "metadata")


def _account_user_id(user_id: str) -> str:
    if not str(user_id or "").strip():
        raise ValueError("account user_id is required")
    return user_id


class AccountDataHandler:
    """Use the host's app-bound persistence and the same collections as the repo."""

    def __init__(self, persistence: Any) -> None:
        self.persistence = persistence

    def _collection(self, app_id: str, name: str) -> Any:
        if self.persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        return self.persistence.collection("messages", name)

    async def _records(
        self, app_id: str, name: str, query: dict[str, Any], fields: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        collection = self._collection(app_id, name)
        projection = {field: 1 for field in fields} or None
        records: list[dict[str, Any]] = []
        cursor = dict(query)
        while True:
            page = await collection.find_many(cursor, limit=_PAGE_SIZE, sort=[("_id", 1)], projection=projection)
            records.extend(
                {field: row.get(field) for field in fields} if fields
                else {key: value for key, value in row.items() if key != "_id"}
                for row in page
            )
            if len(page) < _PAGE_SIZE:
                return records
            cursor = {**query, "_id": {"$gt": page[-1]["_id"]}}

    async def _repoint_previews(self, app_id: str, user_id: str) -> None:
        threads = self._collection(app_id, "threads")
        messages = self._collection(app_id, "messages")
        for thread in await self._records(app_id, "threads", {"last_message.sender_id": user_id}, ("thread_id",)):
            newest = await messages.find_many(
                {"thread_id": thread["thread_id"], "sender_id": {"$ne": user_id}, "is_deleted": {"$ne": True}},
                limit=1,
                sort=[("created_at", -1), ("_id", -1)],
            )
            updates: dict[str, Any] = {"last_message": None, "last_message_at": None}
            if newest:
                row = newest[0]
                updates = {
                    "last_message": {
                        "message_id": row.get("message_id"),
                        "sender_id": row.get("sender_id"),
                        "sender_role": row.get("sender_role"),
                        "body_preview": str(row.get("body") or "")[:_PREVIEW_LENGTH],
                        "sent_at": row.get("created_at"),
                    },
                    "last_message_at": row.get("created_at"),
                }
            # Matching the sender again leaves a preview a peer sent meanwhile untouched.
            await threads.update_one(
                {"thread_id": thread["thread_id"], "last_message.sender_id": user_id},
                {"$set": updates},
            )

    async def _release_threads(self, app_id: str, user_id: str) -> int:
        threads = self._collection(app_id, "threads")
        memberships_removed = 0
        for thread in await self._records(app_id, "threads", {"participant_ids": user_id}, ("thread_id",)):
            result = await threads.update_one(
                {"thread_id": thread["thread_id"], "participant_ids": user_id},
                {"$pull": {"participant_ids": user_id}},
            )
            memberships_removed += int(result.modified_count)
        # A title the user wrote is their content; a support thread's title is its opening message.
        for thread in await self._records(app_id, "threads", {"created_by": user_id}, ("thread_id", "title")):
            released: dict[str, Any] = {"created_by": _RELEASED_CREATOR}
            if str(thread.get("title") or "").strip():
                released["title"] = _RELEASED_TITLE
            await threads.update_one({"thread_id": thread["thread_id"], "created_by": user_id}, {"$set": released})
        # Module dispatch also stamps the acting user onto inserted rows as scope metadata.
        for thread in await self._records(app_id, "threads", {"user_id": user_id}, ("thread_id",)):
            await threads.update_one({"thread_id": thread["thread_id"], "user_id": user_id}, {"$unset": {"user_id": ""}})
        return memberships_removed

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        user_id = _account_user_id(user_id)
        threads = self._collection(app_id, "threads")
        messages = self._collection(app_id, "messages")
        threads_deleted = 0
        for thread in await self._records(app_id, "threads", {"created_by": user_id}, ("thread_id",)):
            peer_messages = await messages.count({
                "thread_id": thread["thread_id"], "sender_id": {"$ne": user_id},
            })
            if peer_messages == 0:
                result = await threads.delete_one({"thread_id": thread["thread_id"], "created_by": user_id})
                threads_deleted += int(result.deleted_count)
        messages_result = await messages.delete_many({"sender_id": user_id})
        await self._repoint_previews(app_id, user_id)
        memberships_removed = await self._release_threads(app_id, user_id)
        reads_result = await self._collection(app_id, "thread_reads").delete_many({"user_id": user_id})
        return {
            "threads_deleted": threads_deleted,
            "messages_deleted": int(messages_result.deleted_count),
            "memberships_removed": memberships_removed,
            "read_state_deleted": int(reads_result.deleted_count),
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        user_id = _account_user_id(user_id)
        return {
            "threads_created": await self._records(app_id, "threads", {"created_by": user_id}, _CREATED_THREAD_FIELDS),
            "messages_sent": await self._records(app_id, "messages", {"sender_id": user_id}),
            "thread_reads": await self._records(app_id, "thread_reads", {"user_id": user_id}),
            "thread_memberships": await self._records(
                app_id, "threads", {"participant_ids": user_id}, _THREAD_FIELDS,
            ),
        }
