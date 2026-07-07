from __future__ import annotations

from typing import Any

from .service import ContactsService


class ContactsHandler:
    def __init__(self) -> None:
        self.service = ContactsService()

    async def list_contacts(self, ctx, *, before: str | None = None, limit: int = 20) -> dict[str, Any]:
        return await self.service.list_contacts(ctx, before=before, limit=limit)

    async def add_contact(self, ctx, *, contact_user_id: str) -> dict[str, Any]:
        return await self.service.add_contact(ctx, contact_user_id=contact_user_id)

    async def remove_contact(self, ctx, *, contact_user_id: str) -> dict[str, Any]:
        return await self.service.remove_contact(ctx, contact_user_id=contact_user_id)

    async def get_contact(self, ctx, *, contact_user_id: str) -> dict[str, Any]:
        return await self.service.get_contact(ctx, contact_user_id=contact_user_id)
