from __future__ import annotations

from typing import Any

_SCOPE_TYPES = {"app", "workspace"}


def actor_id(ctx) -> str:
    return str(getattr(ctx, "user_id", None) or "anonymous").strip() or "anonymous"


def is_participant(thread: dict[str, Any], user_id: str) -> bool:
    return user_id in (thread.get("participant_ids") or [])


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _is_local_development(ctx) -> bool:
    authority = getattr(ctx, "dispatch_authority", None)
    return getattr(authority, "kind", None) == "local_development"


def _verified_workspace_id(ctx) -> str:
    principal = getattr(getattr(ctx, "persistence", None), "principal", None)
    if principal is None or getattr(principal, "source", None) != "authenticated":
        return ""
    verified = _clean(principal.workspace_id)
    if verified and _clean(getattr(ctx, "workspace_id", None)) not in ("", verified):
        raise PermissionError("workspace message dispatch must match the verified workspace")
    tenant = _clean(getattr(ctx, "tenant_id", None))
    if verified and tenant and tenant != _clean(principal.tenant_id):
        raise PermissionError("workspace message dispatch must match the verified tenant")
    return verified


def _scope_authority(ctx, scope_type: str) -> str:
    if scope_type == "workspace":
        if _is_local_development(ctx):
            return _clean(getattr(ctx, "workspace_id", None))
        return _verified_workspace_id(ctx)
    app_id = _clean(getattr(ctx, "app_id", None))
    persistence_app_id = _clean(getattr(getattr(ctx, "persistence", None), "app_id", None))
    if persistence_app_id and app_id != persistence_app_id:
        raise PermissionError("app message dispatch must match the current app")
    return app_id


def resolve_scope(ctx, *, scope_type: str | None = None, scope_id: str | None = None) -> tuple[str, str | None]:
    resolved_type = _clean(scope_type) or "app"
    if resolved_type not in _SCOPE_TYPES:
        resolved_type = "app"

    authorized_id = _scope_authority(ctx, resolved_type)
    requested_id = _clean(scope_id)
    if not authorized_id and (resolved_type == "app" or not _is_local_development(ctx)):
        raise PermissionError(f"{resolved_type} message scope requires a verified identity")
    if requested_id and authorized_id and requested_id != authorized_id:
        raise PermissionError(f"{resolved_type} message scope must match the current context")
    resolved_id = authorized_id or requested_id
    if not resolved_id:
        raise PermissionError(f"{resolved_type} message scope is required")
    return resolved_type, resolved_id


def current_scope_query(ctx, *, scope_type: str | None = None, scope_id: str | None = None) -> dict[str, Any]:
    resolved_type, resolved_id = resolve_scope(ctx, scope_type=scope_type, scope_id=scope_id)
    query: dict[str, Any] = {"scope_type": resolved_type}
    if resolved_id is not None:
        query["scope_id"] = resolved_id
    return query


def thread_identity_queries(ctx, *, thread_id: str) -> list[dict[str, Any]]:
    queries = [{"thread_id": thread_id, **current_scope_query(ctx, scope_type="app")}]
    if _scope_authority(ctx, "workspace"):
        queries.append({"thread_id": thread_id, **current_scope_query(ctx, scope_type="workspace")})
    return queries


def participant_thread_query(ctx, **extra: Any) -> dict[str, Any]:
    query: dict[str, Any] = {"participant_ids": actor_id(ctx), **current_scope_query(ctx)}
    query.update({key: value for key, value in extra.items() if value is not None})
    return query
