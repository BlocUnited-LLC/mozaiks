from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest

from mozaiksai.core.session.model import PendingDecisionAction, PendingHarnessDecision, SessionState
from mozaiksai.core.session.persistence import SessionStateStore
from mozaiksai.core.session.router import SessionRouter
from tests.test_session_router import _FakePersistence


async def _pending_router(target="target-a", persistence=None, decision_overrides=None):
    persistence = persistence or _FakePersistence()
    store = SessionStateStore(persistence)
    router = SessionRouter(persistence=persistence, store=store, target_app_id=target)
    await store.upsert(SessionState(
        session_id=store.session_id_for_scope("host", "alice", target),
        app_id="host", user_id="alice", target_app_id=target,
        active_revision_id="rev-1", active_change_request_id="change-1",
    ))
    decision = PendingHarnessDecision(
        decision_id="decision", decision_type="scope_selection", message="Apply these files",
        rationale="Bounded patch", revision_id="rev-1", change_request_id="change-1",
        selected_paths=["ui/main.jsx"], trigger_payload={"artifact_version_id": "artifact-1"},
    )
    for key, value in (decision_overrides or {}).items():
        setattr(decision, key, deepcopy(value))
    snapshot = await router.mark_pending_harness_decision(
        app_id="host", user_id="alice",
        pending_decision=decision,
    )
    return router, store, persistence, snapshot["pending_harness_decision"]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_overrides", [{}, {"rationale": "  Bounded patch\n", "journey_id": ""}])
async def test_two_confirmations_reading_same_decision_have_one_winner(monkeypatch, decision_overrides):
    router, store, _, pending = await _pending_router(decision_overrides=decision_overrides)
    barrier = asyncio.Barrier(2)
    original_load = store.load

    async def simultaneous_load(**kwargs):
        state = await original_load(**kwargs)
        await barrier.wait()
        return state

    monkeypatch.setattr(store, "load", simultaneous_load)
    results = await asyncio.gather(*(
        router.resolve_pending_harness_decision(
            app_id="host", user_id="alice", decision_id="decision", action_id="apply_proposed_scope",
            expected_pending_decision=pending,
        ) for _ in range(2)
    ), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    state = await original_load(app_id="host", user_id="alice", target_app_id="target-a")
    assert state.pending_harness_decision is None
    assert state.lifecycle_state.value == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,raw,normalized", [
    ("message", "  Apply these files\n", "Apply these files"),
    ("rationale", "\n Bounded patch \n", "Bounded patch"),
    ("journey_id", "", None),
    ("clarification_question", "  ", None),
    ("requested_workflow_id", "", None),
    ("recommended_workflow_id", " AppWorkbench ", "AppWorkbench"),
    ("selected_paths", [" ui/main.jsx ", ""], ["ui/main.jsx"]),
    ("actions", [PendingDecisionAction(
        action_id=" apply_proposed_scope ", label=" Apply scope ",
        action_type=" confirm_workflow ", workflow_id=" AppWorkbench ",
        metadata={"original_request": "  keep user text\n"},
    )], [{
        "action_id": "apply_proposed_scope", "label": "Apply scope",
        "action_type": "confirm_workflow", "workflow_id": "AppWorkbench",
        "metadata": {"original_request": "  keep user text\n"},
    }]),
])
async def test_pending_decision_write_reload_and_expected_snapshot_share_normalization(field, raw, normalized):
    router, store, persistence, pending = await _pending_router(decision_overrides={field: raw})
    coll = await persistence._coll("SessionRouterState")
    saved = deepcopy(coll._docs[store.session_id_for_scope("host", "alice", "target-a")])
    loaded = await store.load(app_id="host", user_id="alice", target_app_id="target-a")

    result = await router.resolve_pending_harness_decision(
        app_id="host", user_id="alice", decision_id="decision", action_id="apply_proposed_scope",
        expected_pending_decision=pending,
    )

    assert result["pending_harness_decision"] is None
    assert pending[field] == normalized
    assert saved["pending_harness_decision"] == asdict(loaded.pending_harness_decision)
    assert saved["pending_harness_decision"][field] == normalized
    with pytest.raises(ValueError, match="No pending"):
        await router.resolve_pending_harness_decision(app_id="host", user_id="alice", decision_id="decision")


@pytest.mark.asyncio
async def test_normalized_decision_is_consumable_without_client_snapshot():
    router, _, _, _ = await _pending_router(decision_overrides={"rationale": " Bounded patch\n", "journey_id": ""})
    result = await router.resolve_pending_harness_decision(
        app_id="host", user_id="alice", decision_id="decision", accepted=False,
    )
    assert result["accepted"] is False
    assert result["pending_harness_decision"] is None


@pytest.mark.asyncio
async def test_decision_normalization_preserves_opaque_payloads_and_build_binding():
    original = {"request": "  keep user whitespace\n", "optional": "", "nested": {"value": " "}}
    metadata = {**original, "build_registry_id": "build-123"}
    router, store, _, pending = await _pending_router(decision_overrides={
        "rationale": " Bounded patch\n", "metadata": metadata,
        "context_variables": original, "trigger_payload": original,
    })
    loaded = await store.load(app_id="host", user_id="alice", target_app_id="target-a")
    assert loaded.pending_harness_decision.metadata == pending["metadata"] == metadata
    for field in ("context_variables", "trigger_payload"):
        assert getattr(loaded.pending_harness_decision, field) == pending[field] == original
    result = await router.resolve_pending_harness_decision(
        app_id="host", user_id="alice", decision_id="decision", expected_pending_decision=pending,
    )
    assert result["pending_harness_decision"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["decision_id", "decision_type", "message", "rationale"])
async def test_invalid_pending_decision_cannot_overwrite_existing_session(field):
    router, store, persistence, _ = await _pending_router()
    coll = await persistence._coll("SessionRouterState")
    before = deepcopy(coll._docs)
    state = await store.load(app_id="host", user_id="alice", target_app_id="target-a")
    setattr(state.pending_harness_decision, field, " \n")
    with pytest.raises(ValueError, match="Pending harness decision"):
        await store.upsert(state)
    assert coll._docs == before
    snapshot = await router.get_session_snapshot(app_id="host", user_id="alice")
    assert snapshot["pending_harness_decision"][field] == before[state.session_id]["pending_harness_decision"][field]


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_consumption_preserves_other_session_fields_and_rejects_replay(accepted):
    router, store, persistence, pending = await _pending_router()
    coll = await persistence._coll("SessionRouterState")
    doc = coll._docs[store.session_id_for_scope("host", "alice", "target-a")]
    doc["unrelated_field"] = {"preserve": True}
    before = deepcopy(doc)
    result = await router.resolve_pending_harness_decision(
        app_id="host", user_id="alice", decision_id="decision", action_id="apply_proposed_scope",
        accepted=accepted, expected_pending_decision=pending,
    )
    assert result["accepted"] is accepted
    assert result["pending_harness_decision"] is None
    assert ("resolved" if accepted else "dismissed") in result["last_route_explanation"]
    after = coll._docs[doc["_id"]]
    changed = {key for key in before if before[key] != after[key]}
    assert changed - {"updated_at"} == {"pending_harness_decision", "lifecycle_state", "last_route_explanation"}
    assert after["updated_at"] >= before["updated_at"]
    with pytest.raises(ValueError, match="No pending"):
        await router.resolve_pending_harness_decision(app_id="host", user_id="alice", decision_id="decision")


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("selected_paths", ["other.jsx"]), ("revision_id", "rev-other"),
    ("change_request_id", "change-other"), ("trigger_payload", {"artifact_version_id": "other"}),
])
async def test_stale_validated_snapshot_cannot_consume_changed_decision(field, value):
    router, store, persistence, pending = await _pending_router()
    coll = await persistence._coll("SessionRouterState")
    doc = coll._docs[store.session_id_for_scope("host", "alice", "target-a")]
    doc["pending_harness_decision"][field] = value
    before = deepcopy(doc)
    with pytest.raises(ValueError, match="changed before confirmation"):
        await router.resolve_pending_harness_decision(
            app_id="host", user_id="alice", decision_id="decision", expected_pending_decision=pending,
        )
    assert coll._docs[doc["_id"]] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("app,user,target,decision", [
    ("foreign", "alice", "target-a", "decision"), ("host", "bob", "target-a", "decision"),
    ("host", "alice", "target-b", "decision"), ("host", "alice", None, "decision"),
    ("host", "alice", "target-a", "stale"),
])
async def test_wrong_scope_or_decision_neither_consumes_nor_creates_session(app, user, target, decision):
    router, _, persistence, _ = await _pending_router()
    coll = await persistence._coll("SessionRouterState")
    before = deepcopy(coll._docs)
    with pytest.raises(ValueError):
        await router.for_target(target).resolve_pending_harness_decision(app_id=app, user_id=user, decision_id=decision)
    assert coll._docs == before


@pytest.mark.asyncio
@pytest.mark.parametrize("active_key", ["active_revision_id", "active_change_request_id"])
async def test_prevalidated_decision_cannot_admit_when_active_revision_was_cleared(active_key):
    router, store, persistence, pending = await _pending_router()
    coll = await persistence._coll("SessionRouterState")
    doc = coll._docs[store.session_id_for_scope("host", "alice", "target-a")]
    doc[active_key] = None
    before = deepcopy(doc)
    with pytest.raises(ValueError, match="no longer active"):
        await router.resolve_pending_harness_decision(
            app_id="host", user_id="alice", decision_id="decision", expected_pending_decision=pending,
        )
    assert coll._docs[doc["_id"]] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["pending", "revision", "change", "lifecycle", "target", "owner"])
async def test_conditional_write_rejects_change_after_validation(monkeypatch, mutation):
    router, store, persistence, pending = await _pending_router()
    coll = await persistence._coll("SessionRouterState")
    doc = coll._docs[store.session_id_for_scope("host", "alice", "target-a")]
    original_update = coll.update_one

    async def change_then_update(query, update, upsert=False):
        assert upsert is False
        if mutation == "pending":
            doc["pending_harness_decision"]["selected_paths"] = ["replacement.jsx"]
        else:
            field = {
                "revision": "active_revision_id", "change": "active_change_request_id",
                "lifecycle": "lifecycle_state", "target": "target_app_id", "owner": "user_id",
            }[mutation]
            doc[field] = "replaced"
        return await original_update(query, update, upsert=upsert)

    monkeypatch.setattr(coll, "update_one", change_then_update)
    with pytest.raises(ValueError, match="already resolved or changed"):
        await router.resolve_pending_harness_decision(
            app_id="host", user_id="alice", decision_id="decision", expected_pending_decision=pending,
        )
    assert coll._docs[doc["_id"]]["pending_harness_decision"] is not None


@pytest.mark.asyncio
async def test_existing_transition_dismissal_consumer_uses_atomic_resolution(monkeypatch):
    from mozaiksai.core import session
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts.routers import transitions

    router, _, _, _ = await _pending_router(target=None)
    monkeypatch.setattr(session, "get_session_router", lambda: router)
    response = await transitions.resolve_session_pending_decision(
        transitions.SessionPendingDecisionResolveRequest(decision_id="decision", accepted=False),
        principal=UserPrincipal(user_id="alice", app_id="host", email=None, name=None, roles=[], scopes=[], provider="test", raw_claims={}),
    )
    assert response["session_state"]["accepted"] is False
    assert response["session_state"]["pending_harness_decision"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("second_accepted", [True, False])
@pytest.mark.parametrize("decision_overrides", [{}, {"rationale": "  Bounded patch\n", "journey_id": ""}])
async def test_real_mongo_allows_one_confirmation_or_dismissal_winner(monkeypatch, second_accepted, decision_overrides):
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.getenv("MONGO_URI")
    if not uri:
        pytest.skip("MONGO_URI is not configured")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1500)
    database_name = f"pending_decision_cas_{uuid4().hex}"
    database = None
    try:
        try:
            await client.admin.command("ping")
        except Exception:
            if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
                pytest.fail("Real Mongo is required for pending-decision CAS acceptance")
            pytest.skip("Mongo is unavailable")
        database = client[database_name]
        print(f"CAS_MONGO_DATABASE={database_name}")

        async def collection(name=None):
            return database[name or "ChatSessions"]

        router, store, _, pending = await _pending_router(
            persistence=SimpleNamespace(_coll=collection), decision_overrides=decision_overrides,
        )
        original_load = store.load
        barrier = asyncio.Barrier(2)

        async def simultaneous_load(**kwargs):
            state = await original_load(**kwargs)
            await barrier.wait()
            return state

        monkeypatch.setattr(store, "load", simultaneous_load)
        results = await asyncio.gather(*(
            router.resolve_pending_harness_decision(
                app_id="host", user_id="alice", decision_id="decision", action_id="apply_proposed_scope",
                accepted=accepted, expected_pending_decision=pending,
            ) for accepted in (True, second_accepted)
        ), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in results) == 1, results
        assert sum(isinstance(result, ValueError) for result in results) == 1, results
        monkeypatch.setattr(store, "load", original_load)
        with pytest.raises(ValueError, match="No pending"):
            await router.resolve_pending_harness_decision(app_id="host", user_id="alice", decision_id="decision")
        with pytest.raises(ValueError, match="No pending"):
            await router.for_target("other-target").resolve_pending_harness_decision(app_id="host", user_id="alice", decision_id="decision")
        assert await database["SessionRouterState"].count_documents({}) == 1
        state = await original_load(app_id="host", user_id="alice", target_app_id="target-a")
        assert state.pending_harness_decision is None
        assert state.lifecycle_state.value == "active"
    finally:
        assert database_name.startswith("pending_decision_cas_")
        if database is not None:
            await client.drop_database(database_name)
        client.close()
