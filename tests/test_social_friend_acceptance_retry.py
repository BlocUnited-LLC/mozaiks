from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.build_context.social.templates.modules.friends.backend.service import (
    FriendsService,
)


@pytest.mark.asyncio
async def test_acceptance_retry_completes_both_memberships_before_accepting_request():
    rows = {}
    attempts = 0

    class Collection:
        async def update_one(self, query, update, *, upsert=False):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise RuntimeError("interrupted second membership write")
            key = (query["member_user_id"], query["friend_user_id"])
            rows.setdefault(key, update["$setOnInsert"])

    request = {"request_id": "request", "requester_id": "alice", "recipient_id": "bob", "status": "pending"}

    async def update_status(*args, **kwargs):
        request["status"] = kwargs["status"]
        return True

    service = FriendsService()
    service._requests = SimpleNamespace(get=AsyncMock(side_effect=lambda *args, **kwargs: dict(request)),
                                       update_status=AsyncMock(side_effect=update_status))
    context = SimpleNamespace(user_id="bob", emit=AsyncMock(),
                              persistence=SimpleNamespace(collection=lambda *args: Collection()))
    with pytest.raises(RuntimeError, match="interrupted"):
        await service.accept_friend_request(context, request_id="request")
    assert request["status"] == "pending"
    assert len(rows) == 1
    result = await service.accept_friend_request(context, request_id="request")
    assert result["success"] is True
    assert request["status"] == "accepted"
    assert set(rows) == {("alice", "bob"), ("bob", "alice")}
    assert {row["friendship_id"] for row in rows.values()} == {"fs_request"}
