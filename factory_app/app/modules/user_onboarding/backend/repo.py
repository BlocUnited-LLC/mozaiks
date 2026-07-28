"""Persistence operations for the user_onboarding module."""
from __future__ import annotations

from typing import Any

_MODULE_ID = "user_onboarding"
_ENTITY = "status"

Document = dict[str, Any]


def _collection(ctx):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence not available in this context.")
    return persistence.collection(_MODULE_ID, _ENTITY)


def _norm(v: Any) -> Document | None:
    return dict(v) if v is not None and hasattr(v, "items") else None


class UserOnboardingRepo:
    async def get(self, ctx, *, user_id: str) -> Document | None:
        return _norm(
            await _collection(ctx).find_one({"user_id": user_id})
        )

    async def upsert(self, ctx, *, user_id: str, update: dict) -> None:
        await _collection(ctx).update_one(
            {"user_id": user_id},
            {"$set": update},
            upsert=True,
        )
