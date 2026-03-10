# ==============================================================================
# FILE: mozaikscore/core/routes/admin_users.py
# DESCRIPTION: Admin user management routes — /__mozaiks/admin/users
#              Paginated listing, single user, suspend/unsuspend/reset actions.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/admin_users.py
# ==============================================================================
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional, List, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from mozaikscore.core.database import get_users_collection
from mozaikscore.core.auth import require_admin_or_internal

logger = logging.getLogger("mozaikscore.routes.admin_users")

router = APIRouter(
    prefix="/__mozaiks/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_admin_or_internal)],
)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class UserItem(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    disabled: bool = False
    createdAt: Optional[str] = None
    lastLoginAt: Optional[str] = None

    class Config:
        extra = "ignore"


class UserListResponse(BaseModel):
    items: List[UserItem]
    page: int
    limit: int
    total: int
    pages: int


class ActionRequest(BaseModel):
    action: Literal["suspendUser", "unsuspendUser", "resetPassword"]
    targetIds: List[str] = Field(..., min_length=1, max_length=100)
    params: Optional[dict] = None


class ActionResponse(BaseModel):
    success: bool
    affected: int
    message: str
    errors: Optional[List[dict]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_user(user: dict) -> UserItem:
    created_at = user.get("created_at") or user.get("createdAt")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    last_login = user.get("last_login") or user.get("lastLoginAt") or user.get("last_login_at")
    if isinstance(last_login, datetime):
        last_login = last_login.isoformat()

    return UserItem(
        id=str(user["_id"]),
        username=user.get("username", ""),
        email=user.get("email"),
        disabled=bool(user.get("disabled", False)),
        createdAt=created_at,
        lastLoginAt=last_login,
    )


def _validate_object_id(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid user ID format: {id_str}",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None),
    disabled: Optional[str] = Query(None),
):
    """Paginated user list with optional search/filter."""
    users_coll = get_users_collection()
    query: dict = {}

    if q and q.strip():
        search_regex = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"username": search_regex}, {"email": search_regex}]

    if disabled is not None and disabled.lower() in ("true", "false"):
        query["disabled"] = disabled.lower() == "true"

    total = await users_coll.count_documents(query)
    skip = (page - 1) * limit
    pages = max(1, math.ceil(total / limit))

    cursor = users_coll.find(query).sort("created_at", -1).skip(skip).limit(limit)
    users = await cursor.to_list(length=limit)
    items = [_serialize_user(u) for u in users]

    return UserListResponse(items=items, page=page, limit=limit, total=total, pages=pages)


@router.get("/{user_id}", response_model=UserItem)
async def get_user(user_id: str):
    """Get a single user by ID."""
    oid = _validate_object_id(user_id)
    users_coll = get_users_collection()
    user = await users_coll.find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _serialize_user(user)


@router.post("/action", response_model=ActionResponse)
async def execute_action(request: ActionRequest):
    """Execute admin actions: suspendUser, unsuspendUser, resetPassword."""
    users_coll = get_users_collection()
    oids = []
    for tid in request.targetIds:
        try:
            oids.append(ObjectId(tid))
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid user ID format: {tid}",
            )

    now_iso = datetime.now(tz=timezone.utc).isoformat()

    if request.action == "suspendUser":
        result = await users_coll.update_many(
            {"_id": {"$in": oids}},
            {"$set": {"disabled": True, "updated_at": now_iso}},
        )
        return ActionResponse(success=True, affected=result.modified_count, message=f"Suspended {result.modified_count} user(s)")

    elif request.action == "unsuspendUser":
        result = await users_coll.update_many(
            {"_id": {"$in": oids}},
            {"$set": {"disabled": False, "updated_at": now_iso}},
        )
        return ActionResponse(success=True, affected=result.modified_count, message=f"Unsuspended {result.modified_count} user(s)")

    elif request.action == "resetPassword":
        params = request.params or {}
        new_password = params.get("newPassword")
        if not new_password:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resetPassword requires params.newPassword")
        if len(new_password) < 8:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")

        from passlib.context import CryptContext

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        hashed = pwd_context.hash(new_password)

        result = await users_coll.update_many(
            {"_id": {"$in": oids}},
            {"$set": {"hashed_password": hashed, "updated_at": now_iso}},
        )
        return ActionResponse(success=True, affected=result.modified_count, message=f"Reset password for {result.modified_count} user(s)")

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown action: {request.action}")


@router.get("/schema", response_model=dict)
async def get_schema():
    """Schema for schema-driven admin UIs."""
    return {
        "module": "users",
        "displayName": "Users",
        "description": "Manage application users",
        "listColumns": [
            {"field": "username", "label": "Username", "sortable": True},
            {"field": "email", "label": "Email", "sortable": True},
            {"field": "createdAt", "label": "Created", "sortable": True, "type": "datetime"},
            {"field": "lastLoginAt", "label": "Last Login", "sortable": True, "type": "datetime"},
            {"field": "disabled", "label": "Status", "type": "boolean", "trueLabel": "Suspended", "falseLabel": "Active"},
        ],
        "actions": [
            {"id": "suspendUser", "label": "Suspend User", "icon": "ban", "confirmMessage": "Are you sure you want to suspend this user?", "appliesWhen": {"field": "disabled", "equals": False}, "bulk": True},
            {"id": "unsuspendUser", "label": "Unsuspend User", "icon": "check", "confirmMessage": "Are you sure you want to unsuspend this user?", "appliesWhen": {"field": "disabled", "equals": True}, "bulk": True},
            {"id": "resetPassword", "label": "Reset Password", "icon": "key", "requiresInput": {"fields": [{"name": "newPassword", "type": "password", "label": "New Password", "required": True, "minLength": 8}]}, "bulk": False},
        ],
        "filters": [
            {"field": "disabled", "label": "Status", "type": "select", "options": [{"value": "", "label": "All"}, {"value": "false", "label": "Active"}, {"value": "true", "label": "Suspended"}]},
        ],
        "searchable": True,
        "searchPlaceholder": "Search by username or email...",
    }
