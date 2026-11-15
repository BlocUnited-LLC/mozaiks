from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.build_context.social.templates.modules.friends.backend.service import (
    FriendsService,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["declined", "withdrawn", "removed"])
async def test_acceptance_refuses_a_competing_terminal_transition(winner):
    request = {"request_id": "request", "requester_id": "alice", "recipient_id": "bob",
               "status": "pending", "created_at": "2026-01-01", "updated_at": "2026-01-01"}
    service = FriendsService()

    async def lose_compare_and_set(*args, **kwargs):
        request["status"] = winner
        return False

    service._requests = SimpleNamespace(get=AsyncMock(side_effect=lambda *args, **kwargs: dict(request)),
                                       update_status=AsyncMock(side_effect=lose_compare_and_set))
    context = SimpleNamespace(user_id="bob", emit=AsyncMock())
    result = await service.accept_friend_request(context, request_id="request")
    assert result["success"] is False
    assert request["status"] == winner
    context.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_acceptance_retry_reads_the_single_committed_state_without_extra_writes():
    request = {"request_id": "request", "requester_id": "alice", "recipient_id": "bob",
               "status": "accepted", "created_at": "2026-01-01", "updated_at": "2026-01-02"}
    original = deepcopy(request)
    service = FriendsService()
    update = AsyncMock()
    service._requests = SimpleNamespace(get=AsyncMock(return_value=request), update_status=update)
    context = SimpleNamespace(user_id="bob", emit=AsyncMock())
    result = await service.accept_friend_request(context, request_id="request")
    assert result["success"] is True
    assert result["friendship"]["friend_user_id"] == "alice"
    assert request == original
    update.assert_not_awaited()
    context.emit.assert_not_awaited()
