from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or getattr(ctx, "session_id", None) or ""


def has_permission(ctx, permission: str) -> bool:
    permissions = getattr(ctx, "permissions", None) or []
    return permission in permissions or "ops.admin" in permissions


def can_manage_catalog(ctx) -> bool:
    return has_permission(ctx, "commerce.catalog.manage")


def can_manage_orders(ctx) -> bool:
    return has_permission(ctx, "commerce.orders.manage")


def product_visibility_query(ctx, *, status: str | None = None) -> dict[str, Any]:
    if can_manage_catalog(ctx):
        return {"status": status} if status else {}
    return {"status": "active"}


def cart_scope_query(ctx) -> dict[str, Any]:
    return {"actor_id": actor_id(ctx), "status": "active"}


def order_scope_query(ctx, *, status: str | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if not can_manage_orders(ctx):
        query["actor_id"] = actor_id(ctx)
    if status:
        query["status"] = status
    return query
