from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from factory_app.app.modules.messages.backend.service import MessageService

logger = logging.getLogger(__name__)


class WorkspaceSupportService:
    def __init__(self, messages: MessageService | None = None) -> None:
        self.messages = messages or MessageService()

    async def _emit(self, ctx, event_type: str, payload: dict[str, Any]) -> None:
        emit = getattr(ctx, "emit", None)
        if emit is None:
            return
        result = emit(event_type, payload)
        if inspect.isawaitable(result):
            await result

    async def _request_collection(self, ctx):
        return ctx.persistence.collection("workspace_support", "requests")

    async def _create_message_thread_for_request(
        self,
        ctx,
        *,
        request_id: str,
        message: str,
        page_url: str | None,
        page_title: str | None,
        severity: str,
    ) -> str | None:
        thread_result = await self.messages.create_thread(
            ctx,
            title=page_title or "Support request",
            participant_ids=[],
            thread_type="support",
            related_type="workspace_support.request",
            related_id=request_id,
            metadata={
                "request_id": request_id,
                "page_url": page_url,
                "page_title": page_title,
                "severity": severity,
            },
        )
        thread = thread_result.get("thread") or {}
        thread_id = thread.get("thread_id")
        if not thread_id:
            return None

        await self.messages.send_message(
            ctx,
            thread_id=thread_id,
            body=message,
            sender_role="user",
            metadata={"request_id": request_id},
        )
        return str(thread_id)

    async def _ensure_message_thread_for_request(
        self,
        ctx,
        *,
        request_doc: dict[str, Any],
        request_id: str,
    ) -> str | None:
        existing_thread_id = request_doc.get("message_thread_id")
        if existing_thread_id:
            return str(existing_thread_id)

        ticket_user_id = str(request_doc.get("user_id") or "").strip()
        participant_ids = [ticket_user_id] if ticket_user_id else []
        thread_result = await self.messages.create_thread(
            ctx,
            title=request_doc.get("page_title") or "Support request",
            participant_ids=participant_ids,
            thread_type="support",
            related_type="workspace_support.request",
            related_id=request_id,
            metadata={
                "request_id": request_id,
                "page_url": request_doc.get("page_url"),
                "page_title": request_doc.get("page_title"),
                "severity": request_doc.get("severity"),
            },
        )
        thread = thread_result.get("thread") or {}
        thread_id = thread.get("thread_id")
        if not thread_id:
            return None

        await (await self._request_collection(ctx)).update_one(
            {"request_id": request_id},
            {"$set": {"message_thread_id": thread_id, "updated_at": datetime.now(tz=UTC).isoformat()}},
        )
        return str(thread_id)

    async def create_support_request(
        self,
        ctx,
        *,
        message: str,
        page_url: str | None = None,
        page_title: str | None = None,
        severity: str = "low",
    ) -> dict:
        now = datetime.now(tz=UTC)
        request_id = f"sr_{uuid4().hex}"
        created_at = now.isoformat()

        doc = {
            "request_id": request_id,
            "app_id": ctx.app_id,
            "user_id": ctx.user_id,
            "message": message,
            "page_url": page_url,
            "page_title": page_title,
            "severity": severity,
            "status": "open",
            "created_at": created_at,
            "updated_at": created_at,
            "resolved_at": None,
            "notes": None,
            "message_thread_id": None,
        }

        try:
            await (await self._request_collection(ctx)).insert_one(doc)
        except Exception:
            logger.warning(
                "workspace_support: persistence unavailable, support request %s not stored",
                request_id,
            )

        message_thread_id = None
        try:
            message_thread_id = await self._create_message_thread_for_request(
                ctx,
                request_id=request_id,
                message=message,
                page_url=page_url,
                page_title=page_title,
                severity=severity,
            )
            if message_thread_id:
                await (await self._request_collection(ctx)).update_one(
                    {"request_id": request_id},
                    {"$set": {"message_thread_id": message_thread_id, "updated_at": datetime.now(tz=UTC).isoformat()}},
                )
        except Exception:
            logger.warning("workspace_support: message thread creation failed for request %s", request_id)

        try:
            await self._emit(
                ctx,
                "domain.workspace_support.request_created",
                {
                    "request_id": request_id,
                    "app_id": ctx.app_id,
                    "severity": severity,
                    "message": message,
                    "page_url": page_url,
                    "page_title": page_title,
                    "message_thread_id": message_thread_id,
                },
            )
        except Exception:
            logger.warning(
                "workspace_support: event emission failed for request %s",
                request_id,
            )

        return {
            "request_id": request_id,
            "status": "open",
            "created_at": created_at,
            "message_thread_id": message_thread_id,
        }

    async def list_support_requests(
        self,
        ctx,
        *,
        status: str = "all",
        limit: int = 50,
        scope: str = "app",
        app_id: str | None = None,
    ) -> dict:
        # Always scope to the authenticated user so a user never sees another
        # user's tickets when this action is called from the profile panel.
        query: dict = {"user_id": ctx.user_id}
        if status != "all":
            query["status"] = status

        try:
            requests = await (await self._request_collection(ctx)).find_many(
                query,
                limit=limit,
                sort=[("created_at", -1)],
            )
        except Exception:
            logger.warning("workspace_support: persistence unavailable, returning empty list")
            requests = []

        for req in requests:
            thread_id = req.get("message_thread_id")
            if not thread_id:
                req["messages"] = []
                continue
            try:
                thread_result = await self.messages.get_thread(
                    ctx,
                    thread_id=str(thread_id),
                    message_limit=100,
                    allow_nonparticipant_reader=True,
                )
                req["messages"] = [
                    {
                        "role": m.get("sender_role", "user"),
                        "content": m.get("body", ""),
                        "senderLabel": "Support" if m.get("sender_role") == "operator" else None,
                        "sentAt": m.get("created_at"),
                    }
                    for m in thread_result.get("messages", [])
                ]
            except Exception:
                req["messages"] = []

        return {"requests": requests, "total": len(requests)}

    async def add_support_message(
        self,
        ctx,
        *,
        request_id: str,
        message: str,
        sender_role: str = "user",  # "user" | "operator"
    ) -> dict:
        now = datetime.now(tz=UTC)
        request_doc = None
        try:
            request_doc = await (await self._request_collection(ctx)).find_one({"request_id": request_id})
        except Exception:
            logger.warning("workspace_support: persistence unavailable for request %s", request_id)

        if not request_doc:
            return {"success": False, "error": "support request not found"}

        thread_id = None
        try:
            thread_id = await self._ensure_message_thread_for_request(
                ctx,
                request_doc=dict(request_doc),
                request_id=request_id,
            )
        except Exception:
            logger.warning("workspace_support: message thread lookup failed for request %s", request_id)

        if not thread_id:
            return {"success": False, "error": "message thread unavailable"}

        ticket_user_id = str((request_doc or {}).get("user_id") or "").strip()
        recipient_ids = [ticket_user_id] if sender_role == "operator" and ticket_user_id else None
        result = await self.messages.send_message(
            ctx,
            thread_id=thread_id,
            body=message,
            sender_role=sender_role,
            recipient_ids=recipient_ids,
            metadata={"request_id": request_id},
            allow_nonparticipant_sender=sender_role == "operator",
        )
        if not result.get("success"):
            return result

        message_doc = result.get("message") or {}
        try:
            await (await self._request_collection(ctx)).update_one(
                {"request_id": request_id},
                {"$set": {"updated_at": now.isoformat(), "message_thread_id": thread_id}},
            )
        except Exception:
            logger.warning("workspace_support: request timestamp update failed for %s", request_id)

        try:
            await self._emit(
                ctx,
                "domain.workspace_support.message_added",
                {
                    "request_id": request_id,
                    "message_id": message_doc.get("message_id"),
                    "app_id": ctx.app_id,
                    "sender_role": sender_role,
                    "sender_id": ctx.user_id,
                    "ticket_user_id": ticket_user_id,
                    "message_preview": message[:80],
                    "message_thread_id": thread_id,
                },
            )
        except Exception:
            logger.warning("workspace_support: event emission failed for message %s", message_doc.get("message_id"))

        return {
            "success": True,
            "message_id": message_doc.get("message_id"),
            "message_thread_id": thread_id,
            "created_at": message_doc.get("created_at") or now.isoformat(),
        }

    async def submit_session_feedback(
        self,
        ctx,
        *,
        session_id: str | None = None,
        workflow_name: str | None = None,
        rating: int = 1,
    ) -> dict:
        now = datetime.now(tz=UTC)
        feedback_id = f"fb_{int(now.timestamp())}"
        created_at = now.isoformat()

        doc = {
            "feedback_id": feedback_id,
            "app_id": ctx.app_id,
            "user_id": ctx.user_id,
            "session_id": session_id,
            "workflow_name": workflow_name,
            "rating": rating,
            "created_at": created_at,
        }

        try:
            await ctx.persistence.collection("workspace_support", "feedback").insert_one(doc)
        except Exception:
            logger.warning(
                "workspace_support: persistence unavailable, feedback %s not stored",
                feedback_id,
            )

        if rating == 0:
            try:
                await self._emit(
                    ctx,
                    "domain.workspace_support.negative_feedback",
                    {
                        "feedback_id": feedback_id,
                        "app_id": ctx.app_id,
                        "session_id": session_id,
                        "workflow_name": workflow_name,
                    },
                )
            except Exception:
                logger.warning(
                    "workspace_support: event emission failed for feedback %s",
                    feedback_id,
                )

        return {"feedback_id": feedback_id, "rating": rating, "created_at": created_at}
