"""Greetings service — fixture community pack."""
from __future__ import annotations

GREETING_STYLES = ["formal", "casual", "enthusiastic"]


class GreetingsService:
    async def say_hello(self, *, user_id: str) -> dict:
        return {"message": f"Hello, {user_id}!", "style": "casual"}

    async def list_greetings(self) -> dict:
        return {"styles": GREETING_STYLES}
