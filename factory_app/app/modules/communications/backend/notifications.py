from __future__ import annotations

from typing import Any, Dict, List


async def resolve_thread_recipients(event: Dict[str, Any]) -> List[str]:
    recipient_ids = [str(item).strip() for item in event.get("recipient_ids", []) if str(item).strip()]
    sender_id = str(event.get("sender_id") or "").strip()
    return [recipient_id for recipient_id in recipient_ids if recipient_id != sender_id]


__all__ = ["resolve_thread_recipients"]