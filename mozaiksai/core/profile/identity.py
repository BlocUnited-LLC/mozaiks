"""Account profile identity shared by platform profiles and trusted Factory launches."""

from typing import Any

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE


def account_profile_id(app_id: str, user_id: str) -> str:
    return f"{app_id}:{user_id}"


def account_profile_collection(client: Any) -> Any:
    return client[SYSTEM_DATABASE]["UserProfiles"]


async def read_account_profile_identity(*, app_id: str, user_id: str) -> dict[str, Any] | None:
    """Read backend-owned identity for an exact authenticated execution scope."""
    if not app_id or not user_id:
        raise ValueError("Account identity requires app_id and user_id")
    profile: dict[str, Any] | None = await account_profile_collection(get_mongo_client()).find_one(
        {"_id": account_profile_id(app_id, user_id), "app_id": app_id, "user_id": user_id},
        {"_id": 0, "email": 1},
    )
    return profile
