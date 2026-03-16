# ==============================================================================
# FILE: mozaikscore/core/routes/profile.py
# DESCRIPTION: User profile retrieval and update routes.
# ==============================================================================
import os
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from mozaikscore.core.auth import get_current_user
from mozaikscore.core.database import get_users_collection, get_cached_document, db_cache
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.schemas import UpdateProfileRequest
from mozaikscore.core.state_manager import state_manager

logger = logging.getLogger("mozaikscore.routes.profile")

ENV = os.getenv("ENV", "development")

router = APIRouter(tags=["profile"])


@router.get("/api/user-profile")
async def get_user_profile(user: dict = Depends(get_current_user)):
    cache_key = f"user_profile:{user['user_id']}"
    cached = state_manager.get(cache_key)
    if cached and ENV != "development":
        return cached
    users = get_users_collection()
    user_data = await get_cached_document(users, {"username": user["username"]}, cache_key=f"user:{user['username']}")
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    user_data.pop("hashed_password", None)
    user_data["_id"] = str(user_data["_id"])
    ttl = 60 if ENV == "development" else 300
    state_manager.set(cache_key, user_data, expire_in=ttl)
    return user_data


@router.post("/api/update-profile")
async def update_user_profile(body: UpdateProfileRequest, user: dict = Depends(get_current_user)):
    try:
        data = body.model_dump(exclude_unset=True)
        protected = {"_id", "username", "email", "hashed_password", "user_id"}
        update = {k: v for k, v in data.items() if k not in protected}
        if not update:
            raise HTTPException(status_code=400, detail="No valid fields to update")
        update["updated_at"] = datetime.utcnow().isoformat()
        users = get_users_collection()
        result = await users.update_one({"username": user["username"]}, {"$set": update})
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="User not found or no changes")
        state_manager.delete(f"user_profile:{user['user_id']}")
        db_cache.invalidate(f"user:{user['username']}")
        event_bus.publish("profile_updated", {"user_id": user["user_id"]})
        return {"message": "Profile updated successfully"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error updating profile: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")
