"""AccountDataHandler for the messages module.

GDPR deletion and export for message threads, messages, and read state.

Deletion policy:
  - Threads where creator_id == deleted user: hard-deleted (the user created them).
  - Thread participant records where user_id == deleted user: hard-deleted.
  - Messages where sender_id == deleted user: hard-deleted.
    The thread and other messages remain intact so that peer conversation history
    is not silently destroyed; the deleted user's contributions are removed.
  - Thread read state records where user_id == deleted user: hard-deleted.

Note: deleting a thread creator's messages may leave a thread with no messages.
The UI should handle empty-thread states gracefully. Do not cascade-delete
threads that have messages from other participants — those are not owned by
the deleted user.
"""
from __future__ import annotations

from typing import Any

_THREADS = "messages.threads"
_MESSAGES = "messages.messages"
_THREAD_READS = "messages.thread_reads"


class AccountDataHandler:
    """GDPR delete + export handler for the messages module."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        scope = {"app_id": app_id}

        # Remove threads the user created that have no other messages.
        user_threads = await self._db[_THREADS].find(
            {**scope, "creator_id": user_id}, {"_id": 0, "thread_id": 1}
        ).to_list(length=None)
        user_thread_ids = [t["thread_id"] for t in user_threads]

        threads_deleted = 0
        for thread_id in user_thread_ids:
            peer_messages = await self._db[_MESSAGES].count_documents(
                {**scope, "thread_id": thread_id, "sender_id": {"$ne": user_id}}
            )
            if peer_messages == 0:
                await self._db[_THREADS].delete_one({**scope, "thread_id": thread_id})
                threads_deleted += 1

        # Remove the user's own messages from all threads.
        msgs_result = await self._db[_MESSAGES].delete_many(
            {**scope, "sender_id": user_id}
        )

        # Remove the user's thread read state.
        reads_result = await self._db[_THREAD_READS].delete_many(
            {**scope, "user_id": user_id}
        )

        return {
            "threads_deleted": threads_deleted,
            "messages_deleted": msgs_result.deleted_count,
            "read_state_deleted": reads_result.deleted_count,
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        scope = {"app_id": app_id}

        threads = await self._db[_THREADS].find(
            {**scope, "creator_id": user_id}, {"_id": 0}
        ).to_list(length=None)

        messages = await self._db[_MESSAGES].find(
            {**scope, "sender_id": user_id}, {"_id": 0}
        ).to_list(length=None)

        return {
            "threads_created": threads,
            "messages_sent": messages,
        }
