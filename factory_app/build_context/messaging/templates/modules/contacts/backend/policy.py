from __future__ import annotations


def actor_id(ctx) -> str:
    return str(getattr(ctx, "user_id", "") or "")


def is_admin_context(ctx) -> bool:
    perms = getattr(ctx, "permissions", None) or []
    return "contacts.admin" in perms
