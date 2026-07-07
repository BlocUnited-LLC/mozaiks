from __future__ import annotations

from typing import Any
from uuid import uuid4

from .policy import actor_id
from .repo import ContactRepo
from .schemas import Contact, coerce_limit, serialize_contact, timestamp_now


class ContactsService:
    def __init__(self, contacts: ContactRepo | None = None) -> None:
        self.contacts = contacts or ContactRepo()

    async def list_contacts(
        self,
        ctx,
        *,
        before: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        user_id = actor_id(ctx)
        if not user_id:
            return {"contacts": [], "count": 0, "next_cursor": None}
        page_size = coerce_limit(limit, default=20)
        rows = await self.contacts.list(
            ctx,
            query={"user_id": user_id, "status": "active"},
            limit=page_size + 1,
            before=before,
        )
        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]
        next_cursor = rows[-1]["created_at"] if has_more and rows else None
        return {
            "contacts": [serialize_contact(r) for r in rows],
            "count": len(rows),
            "next_cursor": next_cursor,
        }

    async def add_contact(self, ctx, *, contact_user_id: str) -> dict[str, Any]:
        user_id = actor_id(ctx)
        if not user_id:
            return {"success": False, "error": "user_id is required"}
        target = str(contact_user_id or "").strip()
        if not target:
            return {"success": False, "error": "contact_user_id is required"}
        if target == user_id:
            return {"success": False, "error": "cannot add yourself as a contact"}
        existing = await self.contacts.get(ctx, user_id=user_id, contact_user_id=target)
        if existing and existing.get("status") == "active":
            return {"success": False, "error": "contact already exists"}
        now = timestamp_now()
        if existing:
            await self.contacts.update(ctx, user_id=user_id, contact_user_id=target, updates={"status": "active", "updated_at": now})
        else:
            doc: Contact = {
                "contact_id": str(uuid4()),
                "user_id": user_id,
                "contact_user_id": target,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            await self.contacts.insert(ctx, doc=doc)
        await ctx.emit("app.contacts.contact.added", {"user_id": user_id, "contact_user_id": target})
        return {"success": True}

    async def remove_contact(self, ctx, *, contact_user_id: str) -> dict[str, Any]:
        user_id = actor_id(ctx)
        if not user_id:
            return {"success": False, "error": "user_id is required"}
        target = str(contact_user_id or "").strip()
        if not target:
            return {"success": False, "error": "contact_user_id is required"}
        existing = await self.contacts.get(ctx, user_id=user_id, contact_user_id=target)
        if not existing or existing.get("status") != "active":
            return {"success": False, "error": "contact not found"}
        now = timestamp_now()
        await self.contacts.update(ctx, user_id=user_id, contact_user_id=target, updates={"status": "removed", "updated_at": now})
        await ctx.emit("app.contacts.contact.removed", {"user_id": user_id, "contact_user_id": target})
        return {"success": True}

    async def get_contact(self, ctx, *, contact_user_id: str) -> dict[str, Any]:
        user_id = actor_id(ctx)
        if not user_id:
            return {"contact": None, "is_contact": False}
        target = str(contact_user_id or "").strip()
        existing = await self.contacts.get(ctx, user_id=user_id, contact_user_id=target)
        if existing and existing.get("status") == "active":
            return {"contact": serialize_contact(existing), "is_contact": True}
        return {"contact": None, "is_contact": False}
