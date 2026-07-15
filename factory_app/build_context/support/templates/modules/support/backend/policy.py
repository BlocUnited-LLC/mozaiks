from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return str(getattr(ctx, "user_id", None) or "anonymous").strip() or "anonymous"


def subject_app_id(ctx, value: str | None = None) -> str:
    return str(value or getattr(ctx, "app_id", None) or "default").strip() or "default"


def actor_permissions(ctx) -> list[str] | None:
    return getattr(ctx, "permissions", None)


def has_permission(ctx, permission: str) -> bool:
    permissions = actor_permissions(ctx)
    return permissions is None or permission in permissions


def can_read_support(ctx) -> bool:
    return has_permission(ctx, "support.read") or has_permission(ctx, "support.manage")


def require_support_read(ctx) -> None:
    if not can_read_support(ctx):
        raise PermissionError("support queue access requires support.read")


def require_support_manage(ctx) -> None:
    if not has_permission(ctx, "support.manage"):
        raise PermissionError("support management requires support.manage")


def support_request_query(
    ctx,
    *,
    scope: str = "app",
    status: str | None = None,
    app_id: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if scope == "user":
        query["requester_id"] = actor_id(ctx)
    if scope == "app":
        query["subject_app_id"] = subject_app_id(ctx, app_id)
    elif scope == "workspace" and app_id:
        query["subject_app_id"] = subject_app_id(ctx, app_id)
    if status and status != "all":
        query["status"] = status
    return query
