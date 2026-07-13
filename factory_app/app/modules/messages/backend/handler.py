from __future__ import annotations

from typing import Any


_DEMO_THREADS = [
    {"thread_id": "demo_thread_1", "title": "Alex Rivera",     "thread_type": "dm",    "unread_count": 2, "last_message": {"body_preview": "Hey, did you see the new build drop?"},   "last_message_at": "2026-07-12T09:00:00Z"},
    {"thread_id": "demo_thread_2", "title": "Jordan Kim",      "thread_type": "dm",    "unread_count": 0, "last_message": {"body_preview": "Sounds good, let's sync tomorrow"},       "last_message_at": "2026-07-12T07:30:00Z"},
    {"thread_id": "demo_thread_3", "title": "BlocUnited Team", "thread_type": "group", "unread_count": 5, "last_message": {"body_preview": "Morgan: shipping the widget fix now"},   "last_message_at": "2026-07-12T04:00:00Z"},
    {"thread_id": "demo_thread_4", "title": "Sam Osei",        "thread_type": "dm",    "unread_count": 0, "last_message": {"body_preview": "Check out this listing I found"},        "last_message_at": "2026-07-11T09:00:00Z"},
]


class MessagesHandler:
    """Stub handler — returns demo data so profile tabs render without MongoDB."""

    async def get_unread_summary(self, ctx) -> dict[str, Any]:
        return {"unread_thread_count": 2, "total_thread_count": 4}

    async def list_threads(self, ctx, *, limit: int = 20, before: str | None = None) -> dict[str, Any]:
        return {"threads": _DEMO_THREADS, "next_cursor": None}
