from __future__ import annotations

import inspect
from typing import Any

from .policy import actor_id, is_participant, participant_thread_query
from .repo import MessageRepo, ReadStateRepo, ThreadRepo
from .schemas import (
    MAX_MESSAGE_LENGTH,
    build_message_record,
    build_thread_record,
    coerce_limit,
    message_preview,
    normalize_participant_ids,
    normalize_scope_type,
    normalize_status,
    normalize_string,
    normalize_thread_type,
    timestamp_now,
)


class MessageService:
    def __init__(
        self,
        *,
        threads: ThreadRepo | None = None,
        messages: MessageRepo | None = None,
        reads: ReadStateRepo | None = None,
    ) -> None:
        self.threads = threads or ThreadRepo()
        self.messages = messages or MessageRepo()
        self.reads = reads or ReadStateRepo()

    async def _emit(self, ctx, event_type: str, payload: dict[str, Any]) -> None:
        emit = getattr(ctx, "emit", None)
        if emit is None:
            return
        result = emit(event_type, payload)
        if inspect.isawaitable(result):
            await result

    async def create_thread(
        self,
        ctx,
        *,
        title: str | None = None,
        participant_ids: list[str] | None = None,
        thread_type: str = "group",
        scope_type: str = "app",
        scope_id: str | None = None,
        subject_app_id: str | None = None,
        related_type: str | None = None,
        related_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        created_by = actor_id(ctx)
        resolved_scope_id = scope_id or (getattr(ctx, "workspace_id", None) if scope_type == "workspace" else getattr(ctx, "app_id", None))
        resolved_subject_app_id = subject_app_id or (getattr(ctx, "app_id", None) if scope_type == "app" else None)
        thread = build_thread_record(
            created_by=created_by,
            scope_type=scope_type,
            scope_id=resolved_scope_id,
            subject_app_id=resolved_subject_app_id,
            title=title,
            participant_ids=participant_ids,
            thread_type=thread_type,
            related_type=related_type,
            related_id=related_id,
            metadata=metadata,
        )
        await self.threads.insert(ctx, record=thread)
        await self._emit(
            ctx,
            "domain.messages.thread_created",
            {
                "thread_id": thread["thread_id"],
                "thread_type": thread["thread_type"],
                "scope_type": thread.get("scope_type"),
                "scope_id": thread.get("scope_id"),
                "subject_app_id": thread.get("subject_app_id"),
                "created_by": created_by,
                "related_type": thread.get("related_type"),
                "related_id": thread.get("related_id"),
            },
        )
        return {"success": True, "thread": dict(thread)}

    async def list_threads(
        self,
        ctx,
        *,
        status: str | None = None,
        thread_type: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        subject_app_id: str | None = None,
        related_type: str | None = None,
        related_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        query = participant_thread_query(ctx)
        if status:
            query["status"] = normalize_status(status)
        if thread_type:
            query["thread_type"] = normalize_thread_type(thread_type)
        if scope_type:
            query["scope_type"] = normalize_scope_type(scope_type)
        if scope_id:
            query["scope_id"] = normalize_string(scope_id)
        if subject_app_id:
            query["subject_app_id"] = normalize_string(subject_app_id)
        if related_type:
            query["related_type"] = normalize_string(related_type)
        if related_id:
            query["related_id"] = normalize_string(related_id)
        threads = await self.threads.list(ctx, query=query, limit=coerce_limit(limit))
        return {"threads": threads, "total": len(threads)}

    async def get_thread(
        self,
        ctx,
        *,
        thread_id: str,
        message_limit: int = 50,
        allow_nonparticipant_reader: bool = False,
    ) -> dict[str, Any]:
        thread = await self.threads.get(ctx, thread_id=thread_id)
        if not thread:
            return {"thread": None, "messages": [], "error": "thread not found"}
        if not allow_nonparticipant_reader and not is_participant(thread, actor_id(ctx)):
            return {"thread": None, "messages": [], "error": "access denied"}
        messages = await self.messages.list(
            ctx,
            thread_id=thread_id,
            limit=coerce_limit(message_limit, default=50, maximum=200),
        )
        return {"thread": thread, "messages": messages}

    async def send_message(
        self,
        ctx,
        *,
        thread_id: str,
        body: str,
        message_type: str = "text",
        sender_role: str = "user",
        recipient_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        allow_nonparticipant_sender: bool = False,
    ) -> dict[str, Any]:
        clean_body = normalize_string(body)
        if not clean_body:
            return {"success": False, "error": "body is required"}
        if len(clean_body) > MAX_MESSAGE_LENGTH:
            return {"success": False, "error": f"message exceeds {MAX_MESSAGE_LENGTH} character limit"}

        thread = await self.threads.get(ctx, thread_id=thread_id)
        if not thread:
            return {"success": False, "error": "thread not found"}
        if thread.get("status") != "open":
            return {"success": False, "error": "thread is closed"}

        sender_id = actor_id(ctx)
        participant_ids = list(thread.get("participant_ids") or [])
        if not is_participant(thread, sender_id):
            if not allow_nonparticipant_sender:
                return {"success": False, "error": "access denied"}
            participant_ids = normalize_participant_ids([*participant_ids, sender_id])

        message = build_message_record(
            thread_id=thread_id,
            sender_id=sender_id,
            sender_role=sender_role,
            body=clean_body,
            message_type=message_type,
            metadata=metadata,
        )
        await self.messages.insert(ctx, record=message)

        preview = {
            "message_id": message["message_id"],
            "sender_id": sender_id,
            "sender_role": message["sender_role"],
            "body_preview": message_preview(clean_body),
            "sent_at": message["created_at"],
        }
        await self.threads.update_last_message(
            ctx,
            thread_id=thread_id,
            updated_at=message["created_at"],
            preview=preview,
            participant_ids=participant_ids,
        )

        recipients = normalize_participant_ids(recipient_ids) if recipient_ids is not None else [
            participant_id for participant_id in participant_ids if participant_id != sender_id
        ]
        await self._emit(
            ctx,
            "domain.messages.message_sent",
            {
                "thread_id": thread_id,
                "message_id": message["message_id"],
                "sender_id": sender_id,
                "sender_role": message["sender_role"],
                "body_preview": message_preview(clean_body),
                "sent_at": message["created_at"],
                "recipient_ids": recipients,
                "participant_ids": participant_ids,
                "scope_type": thread.get("scope_type"),
                "scope_id": thread.get("scope_id"),
                "subject_app_id": thread.get("subject_app_id"),
                "related_type": thread.get("related_type"),
                "related_id": thread.get("related_id"),
            },
        )
        return {"success": True, "message": dict(message)}

    async def mark_thread_read(self, ctx, *, thread_id: str) -> dict[str, Any]:
        thread = await self.threads.get(ctx, thread_id=thread_id)
        user_id = actor_id(ctx)
        if not thread or not is_participant(thread, user_id):
            return {"success": False, "error": "thread not found"}
        await self.reads.upsert(ctx, thread_id=thread_id, user_id=user_id, read_at=timestamp_now())
        await self._emit(ctx, "domain.messages.thread_read", {"thread_id": thread_id, "user_id": user_id})
        return {"success": True}
