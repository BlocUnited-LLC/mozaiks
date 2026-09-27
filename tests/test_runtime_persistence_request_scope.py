from __future__ import annotations

import asyncio
from contextvars import copy_context

import pytest

from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal, PersistenceScopeError
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceCollection
from mozaiksai.core.runtime.persistence.ownership import CollectionOwnership
from mozaiksai.core.runtime.persistence.request_scope import (
    bind_persistence_principal,
    current_persistence_principal,
)
from tests.test_runtime_persistence_ownership import RecordingCollection


@pytest.mark.asyncio
async def test_cached_collection_uses_each_active_dispatch_and_fails_outside_it():
    raw = RecordingCollection()
    collection = MongoPersistenceCollection(
        collection=raw, app_id="app-a", ownership=CollectionOwnership("per_user", "created_by"),
        principal=lambda: current_persistence_principal("app-a"),
    )
    for user in ("alice", "bob"):
        with bind_persistence_principal("app-a", PersistencePrincipal(user)):
            await collection.find_one({})
            await collection.insert_one({"title": "new"})
            assert raw.calls[-2][1]["created_by"] == user
            assert raw.calls[-1][1]["created_by"] == user
            assert raw.calls[-1][1]["user_id"] == user
    with pytest.raises(PersistenceScopeError):
        await collection.find_one({})
    with bind_persistence_principal("app-b", PersistencePrincipal("alice")):
        with pytest.raises(PersistenceScopeError):
            await collection.find_one({})


@pytest.mark.asyncio
async def test_nested_execution_restores_parent_and_revokes_inherited_context():
    with bind_persistence_principal("app-a", PersistencePrincipal("alice")):
        with bind_persistence_principal("app-a", PersistencePrincipal("bob")):
            assert current_persistence_principal("app-a").user_id == "bob"
            inherited = copy_context()
        assert current_persistence_principal("app-a").user_id == "alice"
        assert inherited.run(current_persistence_principal, "app-a") is None
    assert current_persistence_principal("app-a") is None


@pytest.mark.asyncio
async def test_concurrent_dispatches_do_not_share_identity():
    ready = asyncio.Event()

    async def execute(user):
        with bind_persistence_principal("app-a", PersistencePrincipal(user)):
            ready.set()
            await asyncio.sleep(0)
            await ready.wait()
            return current_persistence_principal("app-a").user_id

    assert await asyncio.gather(execute("alice"), execute("bob")) == ["alice", "bob"]
