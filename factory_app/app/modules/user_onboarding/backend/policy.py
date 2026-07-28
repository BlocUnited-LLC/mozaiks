"""Scoping helpers for the user_onboarding module."""
from __future__ import annotations


def actor_id(ctx) -> str:
    uid = getattr(ctx, "user_id", None)
    if not uid:
        raise PermissionError("Unauthenticated")
    return uid
