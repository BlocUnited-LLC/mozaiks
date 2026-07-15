from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from factory_app.app.modules.messages.backend.service import MessageService

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_MESSAGES = 20
MAX_TRANSCRIPT_MESSAGE_LENGTH = 1200
SUPPORT_STATUSES = {"open", "resolved"}


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _json_safe_document(value)
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json_safe_document(doc: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in dict(doc).items():
        if key == "_id":
            continue
        safe[str(key)] = _json_safe_value(value)
    return safe


def _clean_transcript_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    if role in {"user", "assistant", "operator", "system"}:
        return role
    if role in {"agent", "ai"}:
        return "assistant"
    return "user"


def _clean_transcript_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(messages[-MAX_TRANSCRIPT_MESSAGES:]):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("message") or item.get("body") or "").strip()
        if not content:
            continue
        cleaned.append(
            {
                "role": _clean_transcript_role(item.get("role") or item.get("sender") or item.get("sender_role")),
                "content": content[:MAX_TRANSCRIPT_MESSAGE_LENGTH],
                "index": index,
            }
        )
    return cleaned


class WorkspaceSupportService:
    def __init__(self, messages: MessageService | None = None) -> None:
        self.messages = messages or MessageService()

    def _permissions(self, ctx) -> list[str] | None:
        return getattr(ctx, "permissions", None)

    def _has_permission(self, ctx, permission: str) -> bool:
        permissions = self._permissions(ctx)
        return permissions is None or permission in permissions

    def _can_read_support(self, ctx) -> bool:
        return self._has_permission(ctx, "workspace_support.read") or self._has_permission(
            ctx,
            "workspace_support.manage",
        )

    def _can_manage_support(self, ctx) -> bool:
        return self._has_permission(ctx, "workspace_support.manage")

    def _is_ticket_owner(self, ctx, request_doc: dict[str, Any] | None) -> bool:
        if not request_doc:
            return False
        ticket_user_id = str(request_doc.get("user_id") or "").strip()
        current_user_id = str(getattr(ctx, "user_id", "") or "").strip()
        return bool(ticket_user_id and current_user_id and ticket_user_id == current_user_id)

    def _can_mutate_request(self, ctx, request_doc: dict[str, Any] | None) -> bool:
        return self._can_manage_support(ctx) or self._is_ticket_owner(ctx, request_doc)

    async def _emit(self, ctx, event_type: str, payload: dict[str, Any]) -> None:
        emit = getattr(ctx, "emit", None)
        if emit is None:
            return
        result = emit(event_type, payload)
        if inspect.isawaitable(result):
            await result

    def _request_collection(self, ctx):
        return ctx.persistence.collection("workspace_support", "requests")

    def _subject_app_id(self, ctx, value: str | None = None) -> str:
        return str(value or getattr(ctx, "app_id", None) or "default").strip() or "default"

    async def _create_message_thread_for_request(
        self,
        ctx,
        *,
        request_id: str,
        message: str,
        conversation_transcript: list[dict[str, Any]] | None,
        page_url: str | None,
        page_title: str | None,
        severity: str,
        subject_app_id: str,
    ) -> str | None:
        transcript = _clean_transcript_messages(conversation_transcript)
        logger.info(
            "workspace_support: creating message thread request_id=%s subject_app_id=%s user_id=%s severity=%s transcript_count=%s",
            request_id,
            subject_app_id,
            getattr(ctx, "user_id", None),
            severity,
            len(transcript),
        )
        thread_result = await self.messages.create_thread(
            ctx,
            title=page_title or "Support request",
            participant_ids=[],
            thread_type="support",
            scope_type="app",
            scope_id=subject_app_id,
            subject_app_id=subject_app_id,
            related_type="workspace_support.request",
            related_id=request_id,
            metadata={
                "request_id": request_id,
                "subject_app_id": subject_app_id,
                "page_url": page_url,
                "page_title": page_title,
                "severity": severity,
                "transcript_count": len(transcript),
            },
        )
        thread = thread_result.get("thread") or {}
        thread_id = thread.get("thread_id")
        if not thread_id:
            logger.warning(
                "workspace_support: message thread missing from create result request_id=%s result_keys=%s",
                request_id,
                sorted(thread_result.keys()),
            )
            return None

        persisted_transcript = False
        for transcript_message in transcript:
            await self.messages.send_message(
                ctx,
                thread_id=thread_id,
                body=transcript_message["content"],
                sender_role=transcript_message["role"],
                metadata={
                    "request_id": request_id,
                    "source": "support_escalation_transcript",
                    "transcript_index": transcript_message["index"],
                },
            )
            persisted_transcript = True

        if not persisted_transcript:
            await self.messages.send_message(
                ctx,
                thread_id=thread_id,
                body=message,
                sender_role="user",
                metadata={"request_id": request_id},
            )
        logger.info(
            "workspace_support: message thread created request_id=%s thread_id=%s subject_app_id=%s participant_count=%s transcript_count=%s",
            request_id,
            thread_id,
            subject_app_id,
            len(thread.get("participant_ids") or []),
            len(transcript),
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
            logger.info(
                "workspace_support: using existing message thread request_id=%s thread_id=%s",
                request_id,
                existing_thread_id,
            )
            return str(existing_thread_id)

        ticket_user_id = str(request_doc.get("user_id") or "").strip()
        participant_ids = [ticket_user_id] if ticket_user_id else []
        subject_app_id = self._subject_app_id(ctx, request_doc.get("subject_app_id") or request_doc.get("app_id"))
        thread_result = await self.messages.create_thread(
            ctx,
            title=request_doc.get("page_title") or "Support request",
            participant_ids=participant_ids,
            thread_type="support",
            scope_type="app",
            scope_id=subject_app_id,
            subject_app_id=subject_app_id,
            related_type="workspace_support.request",
            related_id=request_id,
            metadata={
                "request_id": request_id,
                "subject_app_id": subject_app_id,
                "page_url": request_doc.get("page_url"),
                "page_title": request_doc.get("page_title"),
                "severity": request_doc.get("severity"),
            },
        )
        thread = thread_result.get("thread") or {}
        thread_id = thread.get("thread_id")
        if not thread_id:
            logger.warning(
                "workspace_support: failed to ensure message thread request_id=%s",
                request_id,
            )
            return None

        await self._request_collection(ctx).update_one(
            {"request_id": request_id},
            {"$set": {"message_thread_id": thread_id, "updated_at": datetime.now(tz=UTC).isoformat()}},
        )
        logger.info(
            "workspace_support: ensured message thread request_id=%s thread_id=%s subject_app_id=%s ticket_user_id=%s",
            request_id,
            thread_id,
            subject_app_id,
            ticket_user_id or None,
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
        app_id: str | None = None,
        conversation_transcript: list[dict[str, Any]] | None = None,
    ) -> dict:
        now = datetime.now(tz=UTC)
        request_id = f"sr_{uuid4().hex}"
        created_at = now.isoformat()
        subject_app_id = self._subject_app_id(ctx, app_id)
        transcript = _clean_transcript_messages(conversation_transcript)

        doc = {
            "request_id": request_id,
            "subject_app_id": subject_app_id,
            "user_id": ctx.user_id,
            "message": message,
            "page_url": page_url,
            "page_title": page_title,
            "severity": severity,
            "status": "open",
            "created_at": created_at,
            "updated_at": created_at,
            "resolved_at": None,
            "resolved_by": None,
            "last_message_at": created_at,
            "last_message_by_role": "user" if not transcript else transcript[-1]["role"],
            "last_operator_response_at": None,
            "last_user_message_at": created_at,
            "notes": None,
            "message_thread_id": None,
            "conversation_transcript_count": len(transcript),
        }

        logger.info(
            "workspace_support: create_support_request start request_id=%s runtime_app_id=%s subject_app_id=%s user_id=%s severity=%s message_length=%s transcript_count=%s page_title=%s",
            request_id,
            getattr(ctx, "app_id", None),
            subject_app_id,
            getattr(ctx, "user_id", None),
            severity,
            len(str(message or "")),
            len(transcript),
            page_title,
        )
        try:
            await self._request_collection(ctx).insert_one(doc)
            logger.info(
                "workspace_support: support request stored request_id=%s subject_app_id=%s user_id=%s",
                request_id,
                subject_app_id,
                getattr(ctx, "user_id", None),
            )
        except Exception:
            logger.warning(
                "workspace_support: persistence unavailable, support request %s not stored",
                request_id,
                exc_info=True,
            )

        message_thread_id = None
        try:
            message_thread_id = await self._create_message_thread_for_request(
                ctx,
                request_id=request_id,
                message=message,
                conversation_transcript=transcript,
                page_url=page_url,
                page_title=page_title,
                severity=severity,
                subject_app_id=subject_app_id,
            )
            if message_thread_id:
                await self._request_collection(ctx).update_one(
                    {"request_id": request_id},
                    {"$set": {"message_thread_id": message_thread_id, "updated_at": datetime.now(tz=UTC).isoformat()}},
                )
        except Exception:
            logger.warning(
                "workspace_support: message thread creation failed for request %s",
                request_id,
                exc_info=True,
            )

        try:
            await self._emit(
                ctx,
                "domain.workspace_support.request_created",
                {
                    "request_id": request_id,
                    "app_id": subject_app_id,
                    "subject_app_id": subject_app_id,
                    "severity": severity,
                    "message": message,
                    "page_url": page_url,
                    "page_title": page_title,
                    "message_thread_id": message_thread_id,
                    "conversation_transcript_count": len(transcript),
                },
            )
        except Exception:
            logger.warning(
                "workspace_support: event emission failed for request %s",
                request_id,
                exc_info=True,
            )

        logger.info(
            "workspace_support: create_support_request complete request_id=%s subject_app_id=%s message_thread_id=%s",
            request_id,
            subject_app_id,
            message_thread_id,
        )
        return {
            "request_id": request_id,
            "app_id": subject_app_id,
            "subject_app_id": subject_app_id,
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
        scope: str = "user",
        app_id: str | None = None,
    ) -> dict:
        scope = str(scope or "user").strip().lower()
        if scope not in {"user", "app", "workspace"}:
            scope = "user"
        if scope != "user" and not self._can_read_support(ctx):
            raise PermissionError("workspace support queue access requires workspace_support.read")

        query: dict = {"user_id": ctx.user_id} if scope == "user" else {}
        if status != "all":
            query["status"] = status
        if scope == "app":
            query["subject_app_id"] = self._subject_app_id(ctx, app_id)
        elif scope == "workspace" and app_id:
            query["subject_app_id"] = self._subject_app_id(ctx, app_id)
        elif scope == "user" and app_id:
            query["subject_app_id"] = self._subject_app_id(ctx, app_id)

        logger.info(
            "workspace_support: list_support_requests start runtime_app_id=%s subject_app_id_filter=%s user_id=%s scope=%s status=%s query=%s",
            getattr(ctx, "app_id", None),
            app_id,
            getattr(ctx, "user_id", None),
            scope,
            status,
            query,
        )
        try:
            requests = await self._request_collection(ctx).find_many(
                query,
                limit=limit,
                sort=[("created_at", -1)],
            )
        except Exception:
            logger.warning(
                "workspace_support: persistence unavailable, returning empty list",
                exc_info=True,
            )
            requests = []

        serialized_requests = [_json_safe_document(dict(req)) for req in requests]
        logger.info(
            "workspace_support: list_support_requests fetched count=%s request_ids=%s thread_ids=%s",
            len(serialized_requests),
            [req.get("request_id") for req in serialized_requests[:10]],
            [req.get("message_thread_id") for req in serialized_requests[:10]],
        )

        for req in serialized_requests:
            thread_id = req.get("message_thread_id")
            if not thread_id:
                req["messages"] = []
                logger.warning(
                    "workspace_support: support request has no message thread request_id=%s",
                    req.get("request_id"),
                )
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
                        "senderLabel": (
                            "Support"
                            if m.get("sender_role") == "operator"
                            else "Assistant"
                            if m.get("sender_role") == "assistant"
                            else None
                        ),
                        "sentAt": m.get("created_at"),
                    }
                    for m in thread_result.get("messages", [])
                ]
                logger.info(
                    "workspace_support: hydrated support messages request_id=%s thread_id=%s message_count=%s thread_error=%s",
                    req.get("request_id"),
                    thread_id,
                    len(req["messages"]),
                    thread_result.get("error"),
                )
            except Exception:
                logger.warning(
                    "workspace_support: failed to hydrate support messages request_id=%s thread_id=%s",
                    req.get("request_id"),
                    thread_id,
                    exc_info=True,
                )
                req["messages"] = []

        for req in serialized_requests:
            subject_app_id = req.get("subject_app_id") or req.get("app_id") or getattr(ctx, "app_id", None)
            req["subject_app_id"] = subject_app_id
            req["app_id"] = subject_app_id

        logger.info(
            "workspace_support: list_support_requests complete count=%s",
            len(serialized_requests),
        )
        return {"requests": serialized_requests, "total": len(serialized_requests)}

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
            request_doc = await self._request_collection(ctx).find_one({"request_id": request_id})
        except Exception:
            logger.warning("workspace_support: persistence unavailable for request %s", request_id)

        if not request_doc:
            logger.warning(
                "workspace_support: add_support_message request not found request_id=%s runtime_app_id=%s user_id=%s",
                request_id,
                getattr(ctx, "app_id", None),
                getattr(ctx, "user_id", None),
            )
            return {"success": False, "error": "support request not found"}

        thread_id = None
        try:
            thread_id = await self._ensure_message_thread_for_request(
                ctx,
                request_doc=dict(request_doc),
                request_id=request_id,
            )
        except Exception:
            logger.warning(
                "workspace_support: message thread lookup failed for request %s",
                request_id,
                exc_info=True,
            )

        if not thread_id:
            logger.warning(
                "workspace_support: add_support_message has no message thread request_id=%s",
                request_id,
            )
            return {"success": False, "error": "message thread unavailable"}

        ticket_user_id = str((request_doc or {}).get("user_id") or "").strip()
        subject_app_id = self._subject_app_id(ctx, request_doc.get("subject_app_id") or request_doc.get("app_id"))
        sender_role = "operator" if sender_role == "operator" else "user"
        current_user_id = str(getattr(ctx, "user_id", "") or "").strip()
        if sender_role == "operator" and not self._can_manage_support(ctx):
            raise PermissionError("operator support replies require workspace_support.manage")
        if (
            sender_role == "user"
            and ticket_user_id
            and current_user_id
            and current_user_id != ticket_user_id
            and not self._can_manage_support(ctx)
        ):
            raise PermissionError("users can only reply to their own support requests")

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
            logger.warning(
                "workspace_support: message send failed request_id=%s thread_id=%s sender_role=%s error=%s",
                request_id,
                thread_id,
                sender_role,
                result.get("error"),
            )
            return result

        message_doc = result.get("message") or {}
        try:
            await self._request_collection(ctx).update_one(
                {"request_id": request_id},
                {
                    "$set": {
                        "updated_at": now.isoformat(),
                        "message_thread_id": thread_id,
                        "last_message_at": message_doc.get("created_at") or now.isoformat(),
                        "last_message_by_role": sender_role,
                        **(
                            {"last_operator_response_at": message_doc.get("created_at") or now.isoformat()}
                            if sender_role == "operator"
                            else {"last_user_message_at": message_doc.get("created_at") or now.isoformat()}
                        ),
                    }
                },
            )
        except Exception:
            logger.warning(
                "workspace_support: request timestamp update failed for %s",
                request_id,
                exc_info=True,
            )

        try:
            await self._emit(
                ctx,
                "domain.workspace_support.message_added",
                {
                    "request_id": request_id,
                    "message_id": message_doc.get("message_id"),
                    "app_id": subject_app_id,
                    "subject_app_id": subject_app_id,
                    "sender_role": sender_role,
                    "sender_id": ctx.user_id,
                    "ticket_user_id": ticket_user_id,
                    "message_preview": message[:80],
                    "message_thread_id": thread_id,
                },
            )
        except Exception:
            logger.warning(
                "workspace_support: event emission failed for message %s",
                message_doc.get("message_id"),
                exc_info=True,
            )

        logger.info(
            "workspace_support: add_support_message complete request_id=%s thread_id=%s message_id=%s sender_role=%s recipient_ids=%s",
            request_id,
            thread_id,
            message_doc.get("message_id"),
            sender_role,
            recipient_ids,
        )
        return {
            "success": True,
            "message_id": message_doc.get("message_id"),
            "message_thread_id": thread_id,
            "created_at": message_doc.get("created_at") or now.isoformat(),
        }

    async def update_support_request_status(
        self,
        ctx,
        *,
        request_id: str,
        status: str,
    ) -> dict:
        clean_request_id = str(request_id or "").strip()
        clean_status = str(status or "").strip().lower()
        if not clean_request_id:
            return {"success": False, "error": "request_id is required"}
        if clean_status not in SUPPORT_STATUSES:
            return {"success": False, "error": "status must be open or resolved"}

        try:
            request_doc = await self._request_collection(ctx).find_one({"request_id": clean_request_id})
        except Exception:
            logger.warning(
                "workspace_support: update_support_request_status lookup failed request_id=%s",
                clean_request_id,
                exc_info=True,
            )
            request_doc = None

        if not request_doc:
            return {"success": False, "error": "support request not found", "request_id": clean_request_id}
        if not self._can_mutate_request(ctx, dict(request_doc)):
            raise PermissionError("updating support request status requires owner or workspace_support.manage")

        now = datetime.now(tz=UTC).isoformat()
        updates: dict[str, Any] = {
            "status": clean_status,
            "updated_at": now,
            "resolved_at": now if clean_status == "resolved" else None,
            "resolved_by": getattr(ctx, "user_id", None) if clean_status == "resolved" else None,
        }
        result = await self._request_collection(ctx).update_one(
            {"request_id": clean_request_id},
            {"$set": updates},
        )

        thread_id = str(request_doc.get("message_thread_id") or "").strip()
        if thread_id:
            try:
                await ctx.persistence.collection("messages", "threads").update_one(
                    {"thread_id": thread_id},
                    {"$set": {"status": clean_status, "updated_at": now}},
                )
            except Exception:
                logger.warning(
                    "workspace_support: failed updating linked thread status request_id=%s thread_id=%s status=%s",
                    clean_request_id,
                    thread_id,
                    clean_status,
                    exc_info=True,
                )

        subject_app_id = request_doc.get("subject_app_id") or request_doc.get("app_id") or getattr(ctx, "app_id", None)
        await self._emit(
            ctx,
            "domain.workspace_support.request_status_changed",
            {
                "request_id": clean_request_id,
                "app_id": subject_app_id,
                "subject_app_id": subject_app_id,
                "status": clean_status,
                "message_thread_id": thread_id or None,
                "changed_by": getattr(ctx, "user_id", None),
            },
        )
        logger.info(
            "workspace_support: update_support_request_status complete request_id=%s status=%s matched=%s thread_id=%s",
            clean_request_id,
            clean_status,
            getattr(result, "matched_count", None),
            thread_id or None,
        )
        return {
            "success": bool(getattr(result, "matched_count", 0) or 0),
            "request_id": clean_request_id,
            "status": clean_status,
            "updated_at": now,
            "message_thread_id": thread_id or None,
        }

    async def delete_support_request(self, ctx, *, request_id: str) -> dict:
        clean_request_id = str(request_id or "").strip()
        if not clean_request_id:
            return {"success": False, "error": "request_id is required"}

        try:
            request_doc = await self._request_collection(ctx).find_one({"request_id": clean_request_id})
        except Exception:
            logger.warning(
                "workspace_support: delete_support_request lookup failed request_id=%s",
                clean_request_id,
                exc_info=True,
            )
            request_doc = None

        if not request_doc:
            logger.warning(
                "workspace_support: delete_support_request not found request_id=%s runtime_app_id=%s user_id=%s",
                clean_request_id,
                getattr(ctx, "app_id", None),
                getattr(ctx, "user_id", None),
            )
            return {"success": False, "error": "support request not found", "request_id": clean_request_id}
        if not self._can_mutate_request(ctx, dict(request_doc)):
            raise PermissionError("deleting support requests requires owner or workspace_support.manage")

        thread_id = str(request_doc.get("message_thread_id") or "").strip()
        logger.info(
            "workspace_support: delete_support_request start request_id=%s thread_id=%s runtime_app_id=%s user_id=%s",
            clean_request_id,
            thread_id or None,
            getattr(ctx, "app_id", None),
            getattr(ctx, "user_id", None),
        )

        deleted_messages = 0
        deleted_reads = 0
        deleted_threads = 0
        if thread_id:
            try:
                result = await ctx.persistence.collection("messages", "messages").delete_many({"thread_id": thread_id})
                deleted_messages = int(getattr(result, "deleted_count", 0) or 0)
            except Exception:
                logger.warning(
                    "workspace_support: delete_support_request failed deleting messages request_id=%s thread_id=%s",
                    clean_request_id,
                    thread_id,
                    exc_info=True,
                )
            try:
                result = await ctx.persistence.collection("messages", "thread_reads").delete_many({"thread_id": thread_id})
                deleted_reads = int(getattr(result, "deleted_count", 0) or 0)
            except Exception:
                logger.warning(
                    "workspace_support: delete_support_request failed deleting read states request_id=%s thread_id=%s",
                    clean_request_id,
                    thread_id,
                    exc_info=True,
                )
            try:
                result = await ctx.persistence.collection("messages", "threads").delete_one({"thread_id": thread_id})
                deleted_threads = int(getattr(result, "deleted_count", 0) or 0)
            except Exception:
                logger.warning(
                    "workspace_support: delete_support_request failed deleting thread request_id=%s thread_id=%s",
                    clean_request_id,
                    thread_id,
                    exc_info=True,
                )

        result = await self._request_collection(ctx).delete_one({"request_id": clean_request_id})
        deleted_requests = int(getattr(result, "deleted_count", 0) or 0)

        try:
            await self._emit(
                ctx,
                "domain.workspace_support.request_deleted",
                {
                    "request_id": clean_request_id,
                    "app_id": request_doc.get("subject_app_id") or request_doc.get("app_id") or getattr(ctx, "app_id", None),
                    "subject_app_id": request_doc.get("subject_app_id") or request_doc.get("app_id"),
                    "message_thread_id": thread_id or None,
                    "deleted_messages": deleted_messages,
                },
            )
        except Exception:
            logger.warning(
                "workspace_support: event emission failed for delete request_id=%s",
                clean_request_id,
                exc_info=True,
            )

        logger.info(
            "workspace_support: delete_support_request complete request_id=%s deleted_requests=%s deleted_threads=%s deleted_messages=%s deleted_reads=%s",
            clean_request_id,
            deleted_requests,
            deleted_threads,
            deleted_messages,
            deleted_reads,
        )
        return {
            "success": deleted_requests > 0,
            "request_id": clean_request_id,
            "message_thread_id": thread_id or None,
            "deleted_requests": deleted_requests,
            "deleted_threads": deleted_threads,
            "deleted_messages": deleted_messages,
            "deleted_reads": deleted_reads,
        }

    async def submit_session_feedback(
        self,
        ctx,
        *,
        session_id: str | None = None,
        workflow_name: str | None = None,
        rating: int = 1,
        app_id: str | None = None,
    ) -> dict:
        now = datetime.now(tz=UTC)
        feedback_id = f"fb_{int(now.timestamp())}"
        created_at = now.isoformat()
        subject_app_id = self._subject_app_id(ctx, app_id)

        doc = {
            "feedback_id": feedback_id,
            "subject_app_id": subject_app_id,
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
                        "app_id": subject_app_id,
                        "subject_app_id": subject_app_id,
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
