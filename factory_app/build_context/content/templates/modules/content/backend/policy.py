def can_manage(ctx):
    user_id = getattr(ctx, "user_id", None)
    return isinstance(user_id, str) and bool(user_id.strip()) and "content.manage" in (getattr(ctx, "permissions", None) or ())


def require_manage(ctx):
    if not can_manage(ctx):
        raise PermissionError("content.manage is required")


def visibility_query(ctx, *, status=None):
    if not can_manage(ctx):
        return {"status": "published"}
    return {"status": status} if status else {}
