from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from .policy import (
    validate_announcement_scope,
    validate_message_body,
    validate_thread_participants,
)
from .repo import CommunicationsRepo
from .schemas import (
    ensure_announcement_payload,
    ensure_message_payload,
    ensure_read_receipt_payload,
    ensure_thread_payload,
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommunicationsService:
    """Business logic scaffold for communications operations."""

    def __init__(self, repo: Optional[CommunicationsRepo] = None) -> None:
        self.repo = repo or CommunicationsRepo()

    async def create_thread(self, payload: Dict[str, Any], *, actor_id: Optional[str] = None) -> Dict[str, Any]:
        thread = ensure_thread_payload(payload)
        validate_thread_participants(thread["participant_ids"])
        enriched = {
            "thread_id": thread.get("thread_id") or f"thread_{uuid4().hex}",
            "title": thread["title"],
            "thread_type": thread["thread_type"],
            "participant_ids": thread["participant_ids"],
            "created_by": actor_id or thread.get("created_by") or "system",
            "created_at": _utc_timestamp(),
        }
        return await self.repo.create_thread_record(enriched)

    async def send_message(self, payload: Dict[str, Any], *, actor_id: Optional[str] = None) -> Dict[str, Any]:
        message = ensure_message_payload(payload)
        validate_message_body(message["body"])
        enriched = {
            "message_id": message.get("message_id") or f"msg_{uuid4().hex}",
            "thread_id": message["thread_id"],
            "sender_id": actor_id or message.get("sender_id") or "system",
            "recipient_ids": message.get("recipient_ids", []),
            "body": message["body"],
            "body_preview": message["body"][:140],
            "attachments": message.get("attachments", []),
            "sent_at": _utc_timestamp(),
        }
        return await self.repo.create_message_record(enriched)

    async def mark_thread_read(self, payload: Dict[str, Any], *, actor_id: Optional[str] = None) -> Dict[str, Any]:
        receipt = ensure_read_receipt_payload(payload)
        enriched = {
            "thread_id": receipt["thread_id"],
            "actor_id": actor_id or receipt.get("actor_id") or "system",
            "message_id": receipt["message_id"],
            "read_at": _utc_timestamp(),
        }
        return await self.repo.update_read_state(enriched)

    async def post_announcement(self, payload: Dict[str, Any], *, actor_id: Optional[str] = None) -> Dict[str, Any]:
        announcement = ensure_announcement_payload(payload)
        validate_announcement_scope(announcement["audience_scope"])
        enriched = {
            "announcement_id": announcement.get("announcement_id") or f"ann_{uuid4().hex}",
            "title": announcement["title"],
            "body": announcement["body"],
            "audience_scope": announcement["audience_scope"],
            "sent_by": actor_id or announcement.get("sent_by") or "system",
            "sent_at": _utc_timestamp(),
        }
        return await self.repo.create_announcement_record(enriched)


__all__ = ["CommunicationsService"]