from __future__ import annotations

import inspect
from typing import Any
from uuid import uuid4

from modules.messages.backend.schemas import MAX_MESSAGE_LENGTH
from modules.messages.backend.service import MessageService

from .policy import (
    actor_id,
    can_read_support,
    require_support_manage,
    require_support_read,
    subject_app_id,
    support_request_query,
)
from .repo import SupportRequestRepo
from .schemas import (
    build_support_request_record,
    coerce_limit,
    normalize_string,
    normalize_status,
    timestamp_now,
)


class SupportService:
    def __init__(
        self,
        *,
        requests: SupportRequestRepo | None = None,
        messages: MessageService | None = None,
    ) -> None:
        self.requests = requests or SupportRequestRepo()
        self.messages = messages or MessageService()

    async def _emit(self, ctx, event_type: str, payload: dict[str, Any]) -> None:
        emit = getattr(ctx, "emit", None)
        if emit is None:
            return
        result = emit(event_type, payload)
        if inspect.isawaitable(result):
            await result

    async def create_support_request(
        self,
        ctx,
        *,
        message: str,
        subject: str | None = None,
        page_url: str | None = None,
        page_title: str | None = None,
        severity: str = "low",
        app_id: str | None = None,
    ) -> dict[str, Any]:
        clean_message = normalize_string(message)
        if not clean_message:
            return {"success": False, "error": "message is required"}
        if len(clean_message) > MAX_MESSAGE_LENGTH:
            return {"success": False, "error": f"message exceeds {MAX_MESSAGE_LENGTH} character limit"}
        target_app_id = subject_app_id(ctx, app_id)
        request_id = f"sr_{uuid4().hex}"
        title = normalize_string(subject) or normalize_string(page_title) or clean_message[:80]
        thread_result = await self.messages.create_thread(
            ctx,
            title=title,
            participant_ids=[],
            thread_type="support",
            scope_type="app",
            subject_app_id=target_app_id,
            related_type="support.request",
            related_id=request_id,
            metadata=[{"key": "request_id", "value": request_id}],
        )
        thread_id = (thread_result.get("thread") or {}).get("thread_id")
        if not thread_id:
            return {"success": False, "error": "support conversation could not be created"}
        sent = await self.messages.send_message(
            ctx,
            thread_id=thread_id,
            body=clean_message,
            allow_support_thread_sender=True,
        )
        if not sent.get("success"):
            return {"success": False, "error": sent.get("error") or "support message could not be sent"}
        record = build_support_request_record(
            request_id=request_id,
            subject_app_id=target_app_id,
            requester_id=actor_id(ctx),
            message=clean_message,
            subject=subject,
            page_url=page_url,
            page_title=page_title,
            severity=severity,
            message_thread_id=thread_id,
        )
        await self.requests.insert(ctx, record=record)
        await self._emit(
            ctx,
            "domain.support.request_created",
            {
                "request_id": record["request_id"],
                "app_id": target_app_id,
                "subject_app_id": target_app_id,
                "requester_id": record.get("requester_id"),
                "severity": record["severity"],
                "message_thread_id": record.get("message_thread_id"),
            },
        )
        return {"success": True, "request": dict(record)}

    async def _authorized_request(self, ctx, *, request_id: str, manage: bool = False) -> dict[str, Any] | None:
        request = await self.requests.get(ctx, request_id=request_id)
        if not request:
            return None
        is_owner = request.get("requester_id") == actor_id(ctx)
        can_manage = getattr(ctx, "permissions", None) is None or "support.manage" in ctx.permissions
        if not is_owner and not (can_manage if manage else can_read_support(ctx)):
            raise PermissionError("support request access denied")
        return request

    async def get_support_conversation(self, ctx, *, request_id: str) -> dict[str, Any]:
        request = await self._authorized_request(ctx, request_id=request_id)
        if not request:
            return {"success": False, "error": "support request not found"}
        result = await self.messages.get_thread(
            ctx,
            thread_id=request["message_thread_id"],
            message_limit=100,
            allow_nonparticipant_reader=request.get("requester_id") != actor_id(ctx),
            allow_support_thread_reader=True,
        )
        if not result.get("thread"):
            return {"success": False, "error": result.get("error") or "support conversation not found"}
        return {"success": True, "thread": result["thread"], "messages": result["messages"]}

    async def reply_support_request(self, ctx, *, request_id: str, body: str) -> dict[str, Any]:
        request = await self._authorized_request(ctx, request_id=request_id, manage=True)
        if not request:
            return {"success": False, "error": "support request not found"}
        if request.get("status") not in {"open", "waiting"}:
            return {"success": False, "error": "support request is closed"}
        is_operator = request.get("requester_id") != actor_id(ctx)
        return await self.messages.send_message(
            ctx,
            thread_id=request["message_thread_id"],
            body=body,
            sender_role="operator" if is_operator else "user",
            allow_nonparticipant_sender=is_operator,
            allow_support_thread_sender=True,
        )

    async def list_support_requests(
        self,
        ctx,
        *,
        status: str = "open",
        scope: str = "user",
        app_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        scope = str(scope or "user").strip().lower()
        if scope not in {"user", "app", "workspace"}:
            scope = "user"
        if scope != "user":
            require_support_read(ctx)
        query = support_request_query(ctx, scope=scope, status=status, app_id=app_id)
        requests = await self.requests.list(ctx, query=query, limit=coerce_limit(limit))
        return {"requests": requests, "total": len(requests)}

    async def update_support_status(
        self,
        ctx,
        *,
        request_id: str,
        status: str,
        assignee_id: str | None = None,
    ) -> dict[str, Any]:
        require_support_manage(ctx)
        normalized_status = normalize_status(status)
        existing = await self.requests.get(ctx, request_id=request_id)
        updates: dict[str, Any] = {"status": normalized_status, "updated_at": timestamp_now()}
        if assignee_id is not None:
            updates["assignee_id"] = str(assignee_id or "").strip() or None
        if normalized_status == "resolved":
            updates["resolved_at"] = timestamp_now()
        updated = await self.requests.update(ctx, request_id=request_id, updates=updates)
        if updated:
            await self._emit(
                ctx,
                "domain.support.status_changed",
                {
                    "request_id": request_id,
                    "status": normalized_status,
                    "assignee_id": assignee_id,
                    "app_id": (existing or {}).get("subject_app_id"),
                    "requester_id": (existing or {}).get("requester_id"),
                    "subject_app_id": (existing or {}).get("subject_app_id"),
                },
            )
        return {"success": bool(updated), "request_id": request_id, "status": normalized_status}
