from __future__ import annotations

import inspect
from typing import Any

from .policy import actor_id, require_support_manage, require_support_read, subject_app_id, support_request_query
from .repo import SupportRequestRepo
from .schemas import (
    build_support_request_record,
    coerce_limit,
    normalize_status,
    timestamp_now,
)


class SupportService:
    def __init__(self, *, requests: SupportRequestRepo | None = None) -> None:
        self.requests = requests or SupportRequestRepo()

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
        message_thread_id: str | None = None,
    ) -> dict[str, Any]:
        if not str(message or "").strip():
            return {"success": False, "error": "message is required"}
        target_app_id = subject_app_id(ctx, app_id)
        record = build_support_request_record(
            subject_app_id=target_app_id,
            requester_id=actor_id(ctx),
            message=message,
            subject=subject,
            page_url=page_url,
            page_title=page_title,
            severity=severity,
            message_thread_id=message_thread_id,
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

    async def link_message_thread(self, ctx, *, request_id: str, message_thread_id: str) -> dict[str, Any]:
        require_support_manage(ctx)
        if not str(message_thread_id or "").strip():
            return {"success": False, "error": "message_thread_id is required"}
        updated = await self.requests.update(
            ctx,
            request_id=request_id,
            updates={"message_thread_id": message_thread_id, "updated_at": timestamp_now()},
        )
        return {"success": bool(updated), "request_id": request_id, "message_thread_id": message_thread_id}

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
