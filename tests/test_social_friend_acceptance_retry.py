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
               "status": "accepted", "created_at": "2026-01-01", "updated_at": "2026-01-02",
               "accepted_at": "2026-01-02", "acceptance_event_id": "persisted-event"}
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
    assert context.emit.await_args.kwargs == {"event_id": "persisted-event"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "receipt"])
async def test_failed_acceptance_emission_replays_the_durable_event_from_a_fresh_service(failure):
    request = {"request_id": "request", "requester_id": "alice", "recipient_id": "bob",
               "status": "pending", "created_at": "2026-01-01", "updated_at": "2026-01-01"}

    async def transition(*args, **kwargs):
        request.update(status=kwargs["status"], updated_at=kwargs["updated_at"],
                       accepted_at=kwargs["updated_at"], acceptance_event_id=kwargs["event_id"])
        return True

    repository = SimpleNamespace(get=AsyncMock(side_effect=lambda *args, **kwargs: dict(request)),
                                 update_status=AsyncMock(side_effect=transition))
    first = FriendsService()
    first._requests = repository
    from mozaiksai.core.events.unified_event_dispatcher import (
        EventDispatchOutcome,
        EventListenerOutcome,
    )

    emitter = AsyncMock(side_effect=RuntimeError("delivery interrupted"))
    if failure == "receipt":
        emitter = AsyncMock(return_value=EventDispatchOutcome("domain.social.friendship.created",
                            (EventListenerOutcome(0, "notification", "failed"),)))
    failed = SimpleNamespace(user_id="bob", emit=emitter)
    with pytest.raises(RuntimeError):
        await first.accept_friend_request(failed, request_id="request")
    assert request["status"] == "accepted"
    retried = FriendsService()
    retried._requests = repository
    recovered = SimpleNamespace(user_id="bob", emit=AsyncMock())
    result = await retried.accept_friend_request(recovered, request_id="request")
    assert result["friendship"]["user_id"] == "bob"
    assert result["friendship"]["friend_user_id"] == "alice"
    assert failed.emit.await_args == recovered.emit.await_args
    assert repository.update_status.await_count == 1


@pytest.mark.asyncio
async def test_first_acceptance_and_retry_return_the_accepting_users_same_view():
    request = {"request_id": "request", "requester_id": "alice", "recipient_id": "bob",
               "status": "pending", "created_at": "2026-01-01", "updated_at": "2026-01-01"}

    async def transition(*args, **kwargs):
        request.update(status=kwargs["status"], updated_at=kwargs["updated_at"],
                       accepted_at=kwargs["updated_at"], acceptance_event_id=kwargs["event_id"])
        return True

    service = FriendsService()
    service._requests = SimpleNamespace(get=AsyncMock(side_effect=lambda *args, **kwargs: dict(request)),
                                       update_status=AsyncMock(side_effect=transition))
    context = SimpleNamespace(user_id="bob", emit=AsyncMock())
    first = await service.accept_friend_request(context, request_id="request")
    retried = await service.accept_friend_request(context, request_id="request")
    assert first == retried
    assert first["friendship"]["user_id"] == "bob"
    assert first["friendship"]["friend_user_id"] == "alice"
    assert context.emit.await_args_list[0] == context.emit.await_args_list[1]
