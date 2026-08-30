"""Farewell service — fixture community pack. Requires greetings pack."""
from __future__ import annotations


class FarewellService:
    async def say_goodbye(self, *, user_id: str) -> dict:
        return {"message": f"Goodbye, {user_id}! Come back soon."}
