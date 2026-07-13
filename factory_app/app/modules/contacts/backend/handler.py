from __future__ import annotations

from typing import Any

_DEMO_CONTACTS = [
    {"contact_id": "demo_1", "contact_user_id": "alex.rivera",  "status": "active", "created_at": "2026-07-01T10:00:00Z"},
    {"contact_id": "demo_2", "contact_user_id": "jordan.kim",   "status": "active", "created_at": "2026-07-03T14:30:00Z"},
    {"contact_id": "demo_3", "contact_user_id": "sam.osei",     "status": "active", "created_at": "2026-07-08T09:15:00Z"},
]


class ContactsHandler:
    """Stub handler — returns demo data so the profile panel renders without MongoDB."""

    async def list_contacts(self, ctx, *, before: str | None = None, limit: int = 20) -> dict[str, Any]:
        return {"contacts": _DEMO_CONTACTS, "count": len(_DEMO_CONTACTS)}
