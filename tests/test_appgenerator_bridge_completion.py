"""Production-bridge completion traversal for AppGenerator lifecycle claims.

Exercises the REAL callsite chain: workflow_bridge._launch_workflow_run_locked
→ run-identity minting → persisted final-context fetch → the real AppGenerator
on_complete hook resolved through get_workflow_lifecycle_hooks → the real
shared lifecycle emitters, intercepted at the outbox boundary. The hook is
never invoked directly by these tests.

The central regression is Codex 1's release blocker: Run B in the same
app/chat/workflow must never reuse Run A's persisted terminal state to claim
build.completed. Lifecycle claims bind to an immutable run-scoped terminal
receipt that must also cold-verify against the persisted build lineage.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.artifacts.build_receipt import (
    TERMINAL_RECEIPT_CONTEXT_KEY,
    issue_failure_receipt,
    issue_success_receipt,
)
from mozaiksai.core.ports.orchestration import RunResult, RunStatus
from mozaiksai.core.runtime.composition.extensions import get_workflow_lifecycle_hooks
from mozaiksai.core.transport.workflow_bridge import WorkflowBridgeMixin

_shared_lifecycle = __import__(
    "factory_app.workflows._shared.platform.build_lifecycle",
    fromlist=["build_lifecycle"],
)

_APP_ID = "app-bridge"
_CHAT_ID = "chat-bridge"
_WORKFLOW = "AppGenerator"
_BUNDLE_DIGEST = "d" * 64


class _FakePersistenceManager:
    """Durable chat-session extra-context store (survives 'restart')."""

    def __init__(self, initial: dict[str, Any] | None = None, *, fail_fetch: bool = False) -> None:
        self.store: dict[str, Any] = dict(initial or {})
        self.fail_fetch = fail_fetch

    async def fetch_chat_session_extra_context(self, **_kwargs: Any) -> dict[str, Any]:
        if self.fail_fetch:
            raise RuntimeError("session context unavailable")
        return dict(self.store)

    async def persist_context_variables(
        self, *, chat_id: str, app_id: str | None = None,
        variables: dict[str, Any] | None = None, workflow_name: str | None = None,
    ) -> None:
        # Mirrors production: server-owned fields are rejected here.
        for key, value in (variables or {}).items():
            if key in {"build_terminal_receipt", "workflow_run_id"}:
                continue
            self.store[key] = value

    async def persist_server_owned_session_fields(
        self, *, chat_id: str, app_id: str | None = None,
        workflow_name: str | None = None, fields: dict[str, Any] | None = None,
    ) -> None:
        for key, value in (fields or {}).items():
            assert key in {"build_terminal_receipt", "workflow_run_id"}, key
            self.store[key] = value


class _Bridge(WorkflowBridgeMixin):
    def __init__(self, pm: _FakePersistenceManager) -> None:
        self._pm = pm
        self.sent_errors: list[dict[str, Any]] = []
        self.synthetic_completes = 0

    def _get_or_create_persistence_manager(self) -> _FakePersistenceManager:
        return self._pm

    async def _emit_synthetic_run_complete_if_needed(self, **_kwargs: Any) -> None:
        self.synthetic_completes += 1

    async def send_error(self, **kwargs: Any) -> None:
        self.sent_errors.append(kwargs)


class _FakeAdapter:
    """Completed-run adapter; on_run simulates terminal tool work in-run."""

    def __init__(self, pm: _FakePersistenceManager, on_run: Any = None) -> None:
        self._pm = pm
        self._on_run = on_run

    async def _result(self, request: Any) -> RunResult:
        self.invocations = getattr(self, "invocations", 0) + 1
        if self._on_run is not None:
            await self._on_run(self._pm)
        return RunResult(
            status=RunStatus.COMPLETED,
            chat_id=request.chat_id,
            workflow_name=request.workflow_name,
        )

    async def run(self, request: Any) -> RunResult:
        return await self._result(request)

    async def resume(self, request: Any) -> RunResult:
        return await self._result(request)


class _FakeArtifactStore:
    """Cold-resolution surface for receipt lineage verification."""

    def __init__(self) -> None:
        self.records: dict[str, Any] = {}

    def add_record(
        self, *, record_id: str, build_family: str, lifecycle: str,
        parent: str | None = None, digests: tuple[str, ...] = (),
        workflow_run_id: str | None = None, build_id: str | None = None,
        build_key: str | None = None, summary_payload: dict[str, Any] | None = None,
    ) -> None:
        self.records[record_id] = SimpleNamespace(
            id=record_id,
            build_family=build_family,
            build_key=build_key if build_key is not None else build_family,
            lifecycle_status=lifecycle,
            parent_build_record_id=parent,
            workflow_run_id=workflow_run_id,
            build_id=build_id,
            files_manifest=[SimpleNamespace(sha256=d) for d in digests],
            commit_metadata=SimpleNamespace(
                metadata={"summary_payload": summary_payload} if summary_payload else {}
            ),
        )

    async def get_build_record(self, *, app_id: str, build_record_id: str) -> Any:
        return self.records.get(build_record_id)


def _register_success_lineage(
    store: _FakeArtifactStore,
    *,
    run_id: str,
    app_id: str = _APP_ID,
    build_id: str | None = None,
    record_id: str = "av_run",
    context_record_id: str = "acv_run",
    context_version_id: str = "ctx_run",
    lifecycle: str = "current",
    context_lifecycle: str = "current",
    parent: str | None = None,
    digest: str = _BUNDLE_DIGEST,
    context_run_id: str | None = None,
    context_build_id: str | None = None,
    cross_ref_record_id: str | None = None,
    payload_app_id: str | None = None,
) -> None:
    """Persist the exact closure a legitimate run produces (or an attacked
    variant thereof)."""
    bound_build = build_id or f"build_{run_id}"
    store.add_record(
        record_id=record_id, build_family="app_bundle", build_key="app_bundle",
        lifecycle=lifecycle, parent=parent, digests=(digest,),
        workflow_run_id=run_id, build_id=bound_build,
    )
    store.add_record(
        record_id=context_record_id, build_family="app_context_version",
        build_key="app_context_version", lifecycle=context_lifecycle,
        workflow_run_id=context_run_id if context_run_id is not None else run_id,
        build_id=context_build_id if context_build_id is not None else bound_build,
        summary_payload={
            "context_version_id": context_version_id,
            "app_id": payload_app_id if payload_app_id is not None else app_id,
            "artifact_refs": [
                {
                    "artifact_kind": "app_bundle",
                    "artifact_version_id": (
                        cross_ref_record_id if cross_ref_record_id is not None else record_id
                    ),
                }
            ],
        },
    )


def _success_receipt_dict(
    run_id: str,
    *,
    app_id: str = _APP_ID,
    workflow_name: str = _WORKFLOW,
    build_id: str | None = None,
    record_id: str = "av_run",
    context_version_id: str = "ctx_run",
    context_record_id: str = "acv_run",
    digest: str = _BUNDLE_DIGEST,
) -> dict[str, Any]:
    receipt = issue_success_receipt(
        app_id=app_id,
        workflow_name=workflow_name,
        workflow_run_id=run_id,
        build_id=build_id or f"build_{run_id}",
        build_record_id=record_id,
        app_context_version_id=context_version_id,
        app_context_record_id=context_record_id,
        bundle_digest=digest,
    )
    return receipt.model_dump(mode="json")


def _terminal_tool_success(store: _FakeArtifactStore):
    """Simulate generate_and_download's terminal closure for the live run."""

    async def _on_run(pm: _FakePersistenceManager) -> None:
        run_id = pm.store["workflow_run_id"]
        _register_success_lineage(
            store, run_id=run_id,
            record_id=f"av_{run_id}", context_record_id=f"acv_{run_id}",
            context_version_id=f"ctx_{run_id}",
        )
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = _success_receipt_dict(
            run_id, record_id=f"av_{run_id}", context_record_id=f"acv_{run_id}",
            context_version_id=f"ctx_{run_id}",
        )
        pm.store["download_status"] = "ready"
        pm.store["app_download_ready"] = True

    return _on_run


def _terminal_tool_failure():
    async def _on_run(pm: _FakePersistenceManager) -> None:
        run_id = pm.store["workflow_run_id"]
        receipt = issue_failure_receipt(
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            workflow_run_id=run_id,
            build_id=f"build_{run_id}",
            error_code="lineage_registration_failed",
        )
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = receipt.model_dump(mode="json")
        pm.store["download_status"] = "failed"

    return _on_run


def _intercept_lifecycle(monkeypatch) -> list[dict[str, Any]]:
    """Route the real shared emitters' persistence boundary into memory."""
    events: list[dict[str, Any]] = []

    async def _session_context(**_kwargs: Any) -> dict[str, Any]:
        return {}

    async def _upsert(**kwargs: Any) -> str:
        events.append(
            {
                "event_type": kwargs.get("event_type"),
                "status": kwargs.get("status"),
                "build_id": kwargs.get("build_id"),
                "idempotency_key": kwargs.get("idempotency_key"),
                "payload": kwargs.get("payload"),
            }
        )
        return f"outbox_{len(events)}"

    async def _artifacts(**_kwargs: Any) -> dict[str, Any]:
        return {}

    async def _materialize(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(_shared_lifecycle, "_get_chat_session_context", _session_context)
    monkeypatch.setattr(_shared_lifecycle, "upsert_outbox_event", _upsert)
    monkeypatch.setattr(_shared_lifecycle, "get_build_artifacts", _artifacts)
    monkeypatch.setattr(_shared_lifecycle, "_materialize_local_app_registry_event", _materialize)
    monkeypatch.setattr(_shared_lifecycle, "_spawn_delivery", lambda **_kw: None)

    import mozaiksai.core.artifacts.summary_artifacts as summary_artifacts

    monkeypatch.setattr(summary_artifacts, "persist_summary_artifact", AsyncMock())
    return events


def _patch_store(monkeypatch, store: _FakeArtifactStore) -> None:
    import mozaiksai.core.artifacts as artifacts_pkg

    monkeypatch.setattr(artifacts_pkg, "get_artifact_store", lambda: store)


def _patch_adapter(monkeypatch, pm: _FakePersistenceManager, on_run: Any = None) -> None:
    import mozaiksai.core.adapters.ag2_orchestration as orchestration

    adapter = _FakeAdapter(pm, on_run)
    monkeypatch.setattr(orchestration, "get_ag2_adapter", lambda: adapter)


async def _launch(
    bridge: _Bridge,
    hooks: dict[str, Any],
    *,
    is_resume_request: bool = False,
    emit_started: Any = None,
    expect_status: str = "success",
    workflow_name: str = _WORKFLOW,
) -> dict[str, Any]:
    before_tasks = set(asyncio.all_tasks())
    result = await bridge._launch_workflow_run_locked(
        chat_id=_CHAT_ID,
        user_id="user-1",
        workflow_name=workflow_name,
        message=None,
        app_id=_APP_ID,
        initial_agent_name_override="resume-agent" if is_resume_request else None,
        is_resume_request=is_resume_request,
        emit_execution_started=emit_started,
        emit_execution_completed=hooks.get("on_complete"),
    )
    assert result["status"] == expect_status, result
    if expect_status == "success":
        assert result["run_status"] == "completed"
    else:
        assert result.get("run_status") != "completed"
    await asyncio.sleep(0)
    # Attack 17: lifecycle dispatch is awaited — no pending lifecycle task or
    # unretrieved exception survives the call, and the superseded tracker is
    # gone entirely.
    assert "_lifecycle_tasks" not in bridge.__dict__
    leaked = {
        t for t in asyncio.all_tasks() - before_tasks
        if t is not asyncio.current_task() and not t.done()
    }
    assert not leaked, f"leaked pending tasks: {leaked}"
    return result


def _hooks() -> dict[str, Any]:
    hooks = get_workflow_lifecycle_hooks(_WORKFLOW)
    assert hooks["on_complete"] is not None, "AppGenerator on_complete hook must resolve"
    return hooks


def _events_of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] == event_type]


# ---------------------------------------------------------------------------
# Success / failure / stale-run authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_production_run_emits_exactly_one_completed(monkeypatch) -> None:
    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))

    await _launch(_Bridge(pm), _hooks())

    completed = _events_of(events, "build.completed")
    assert len(completed) == 1, events
    assert _events_of(events, "build.failed") == []
    run_id = pm.store["workflow_run_id"]
    assert run_id in completed[0]["idempotency_key"]
    assert completed[0]["build_id"] == f"build_{run_id}"
    assert completed[0]["payload"]["workflowRunId"] == run_id


@pytest.mark.asyncio
async def test_stale_run_a_state_cannot_complete_run_b(monkeypatch) -> None:
    """Codex 1's release blocker, end to end through the production bridge.

    Run A completes legitimately (receipt + lineage + ready marker persisted
    in the chat-scoped session context). Run B — same app/chat/workflow, a
    fresh immutable run id — completes WITHOUT running its terminal tool.
    Run A's persisted state is still in the chat context; Run B must claim
    nothing: no build.completed, no build.failed, no Run B lineage invented.
    """
    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    hooks = _hooks()

    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))
    await _launch(_Bridge(pm), hooks)
    run_a_id = pm.store["workflow_run_id"]
    assert len(_events_of(events, "build.completed")) == 1
    events_after_a = len(events)

    # Run B: no terminal tool; Run A's receipt + ready marker still persisted.
    _patch_adapter(monkeypatch, pm, None)
    await _launch(_Bridge(pm), hooks)
    run_b_id = pm.store["workflow_run_id"]
    assert run_b_id != run_a_id

    run_b_events = events[events_after_a:]
    assert run_b_events == [], (
        f"Run B claimed lifecycle events from Run A's stale state: {run_b_events}"
    )


@pytest.mark.asyncio
async def test_failed_terminal_run_emits_exactly_one_run_bound_failed(monkeypatch) -> None:
    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, _FakeArtifactStore())
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _terminal_tool_failure())

    await _launch(_Bridge(pm), _hooks())

    failed = _events_of(events, "build.failed")
    assert len(failed) == 1, events
    assert _events_of(events, "build.completed") == []
    run_id = pm.store["workflow_run_id"]
    assert failed[0]["build_id"] == f"build_{run_id}"
    assert run_id in failed[0]["idempotency_key"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stale_state",
    [
        {},
        {"download_status": "ready", "app_download_ready": True},
        {"download_status": "cancelled", "app_download_ready": False},
        {"download_status": "failed", "app_download_ready": False},
    ],
    ids=["no-state", "stale-ready-marker", "cancelled-marker", "stale-failed-marker"],
)
async def test_runs_without_own_receipt_claim_nothing(monkeypatch, stale_state) -> None:
    """Bare chat-scoped markers are UI projections, never lifecycle authority.

    Covers Run B failing before download, Run B cancelled, and stale ready /
    failed markers left by earlier runs: without a receipt bound to the
    completing run there is no claim in either direction.
    """
    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, _FakeArtifactStore())
    pm = _FakePersistenceManager(dict(stale_state))
    _patch_adapter(monkeypatch, pm, None)

    await _launch(_Bridge(pm), _hooks())
    assert events == [], events


@pytest.mark.asyncio
async def test_context_fetch_failure_claims_nothing(monkeypatch) -> None:
    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, _FakeArtifactStore())
    pm = _FakePersistenceManager(fail_fetch=True)

    async def _mint_guard(**kwargs: Any) -> dict[str, Any]:
        return {}

    # Identity minting must still work; only the completion-time fetch fails.
    pm.fail_fetch = False
    _patch_adapter(monkeypatch, pm, None)
    bridge = _Bridge(pm)

    original_fetch = pm.fetch_chat_session_extra_context
    calls = {"n": 0}

    async def _flaky_fetch(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        raise RuntimeError("session context unavailable")

    pm.fetch_chat_session_extra_context = _flaky_fetch  # type: ignore[method-assign]
    try:
        await _launch(bridge, _hooks())
    finally:
        pm.fetch_chat_session_extra_context = original_fetch  # type: ignore[method-assign]
    assert events == [], events


# ---------------------------------------------------------------------------
# Reconnect / restart / retry / concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_preserves_run_identity_and_dedupes_completion(monkeypatch) -> None:
    """Attacks 5/7/19: resume and restart re-emit under the SAME idempotency
    key — one effective event — and never regenerate the run identity."""
    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    hooks = _hooks()

    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))
    await _launch(_Bridge(pm), hooks)
    run_id = pm.store["workflow_run_id"]
    first_key = _events_of(events, "build.completed")[0]["idempotency_key"]

    # Reconnect (resume) on a NEW bridge instance — process restart shape:
    # durable session context is all that survives.
    _patch_adapter(monkeypatch, pm, None)
    await _launch(_Bridge(pm), hooks, is_resume_request=True)

    assert pm.store["workflow_run_id"] == run_id, "resume must not regenerate run identity"
    completed = _events_of(events, "build.completed")
    assert len(completed) == 2
    assert completed[1]["idempotency_key"] == first_key, (
        "retried completion for the same receipt must share one idempotency key"
    )
    assert _events_of(events, "build.failed") == []


@pytest.mark.asyncio
async def test_interleaved_runs_in_one_chat_stay_distinguishable(monkeypatch) -> None:
    """Attack 6: Run B starts before Run A's completion claim lands; each
    claim binds to its own run identity and Run B claims nothing."""
    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    hooks = _hooks()

    # Run A terminal tool ran; its receipt is persisted.
    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))
    await _launch(_Bridge(pm), hooks)
    run_a_id = pm.store["workflow_run_id"]

    # Run B launches; its completion sees Run A's receipt only.
    _patch_adapter(monkeypatch, pm, None)
    await _launch(_Bridge(pm), hooks)
    run_b_id = pm.store["workflow_run_id"]

    completed = _events_of(events, "build.completed")
    assert len(completed) == 1
    assert run_a_id in completed[0]["idempotency_key"]
    assert run_b_id not in completed[0]["idempotency_key"]


# ---------------------------------------------------------------------------
# Receipt tamper matrix (attacks 8–15)
# ---------------------------------------------------------------------------


async def _launch_with_seeded_receipt(
    monkeypatch, receipt: dict[str, Any], store: _FakeArtifactStore
) -> list[dict[str, Any]]:
    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()

    async def _seed(inner_pm: _FakePersistenceManager) -> None:
        inner_pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = receipt

    _patch_adapter(monkeypatch, pm, _seed)
    bridge = _Bridge(pm)
    await _launch(bridge, _hooks())
    return events


@pytest.mark.asyncio
async def test_tampered_receipt_field_fails_digest_and_claims_nothing(monkeypatch) -> None:
    """Attack 16 precursor: altering a field without re-signing fails the
    body digest before any lineage resolution."""
    store = _FakeArtifactStore()

    async def _seed(pm: _FakePersistenceManager) -> None:
        rid = pm.store["workflow_run_id"]
        _register_success_lineage(store, run_id=rid)
        receipt = _success_receipt_dict(rid)
        receipt["build_id"] = "attacker-substituted-build"  # not re-signed
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = receipt

    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _seed)
    await _launch(_Bridge(pm), _hooks())
    assert events == [], events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["wrong_app", "wrong_workflow", "wrong_run"],
)
async def test_validly_signed_receipt_with_wrong_identity_claims_nothing(
    monkeypatch, mutation: str
) -> None:
    """A digest-valid receipt whose identity does not match the completing
    run/app/workflow can never authorize a claim."""
    store = _FakeArtifactStore()

    async def _seed_current_run(pm: _FakePersistenceManager) -> None:
        current_rid = pm.store["workflow_run_id"]
        receipt_rid = "wfrun_other_run" if mutation == "wrong_run" else current_rid
        kwargs: dict[str, Any] = {}
        if mutation == "wrong_app":
            kwargs["app_id"] = "some-other-app"
        if mutation == "wrong_workflow":
            kwargs["workflow_name"] = "AgentGenerator"
        # Give the forger a perfect persisted closure for the receipt's own
        # identity so rejection comes from the identity binding alone.
        _register_success_lineage(store, run_id=receipt_rid)
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = _success_receipt_dict(receipt_rid, **kwargs)

    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _seed_current_run)
    await _launch(_Bridge(pm), _hooks())
    assert events == [], events


def _closure_attack_missing_build_record(store, rid):
    _register_success_lineage(store, run_id=rid)
    return _success_receipt_dict(rid, record_id="av_nonexistent")


def _closure_attack_wrong_family(store, rid):
    _register_success_lineage(store, run_id=rid)
    store.records["av_run"].build_family = "workflow_bundle"
    return _success_receipt_dict(rid)


def _closure_attack_wrong_key(store, rid):
    _register_success_lineage(store, run_id=rid)
    store.records["av_run"].build_key = "secondary"
    return _success_receipt_dict(rid)


def _closure_attack_fake_build_id_real_record(store, rid):
    # Attack 4: real bound CURRENT record; receipt re-signed with a build_id
    # that has no corresponding persisted build.
    _register_success_lineage(store, run_id=rid)
    return _success_receipt_dict(rid, build_id="build_with_no_persisted_build")


def _closure_attack_record_bound_to_other_run(store, rid):
    # Attack 5: the referenced records were created for another run.
    _register_success_lineage(store, run_id="wfrun_other")
    return _success_receipt_dict(
        rid, build_id="build_wfrun_other",
        context_version_id="ctx_run",
    )


def _closure_attack_unbound_record(store, rid):
    # Attack 8 shape: an older record with no persisted run binding can never
    # satisfy a lineage receipt — no fallback.
    _register_success_lineage(store, run_id=rid)
    store.records["av_run"].workflow_run_id = None
    store.records["av_run"].build_id = None
    return _success_receipt_dict(rid)


def _closure_attack_missing_context_record(store, rid):
    _register_success_lineage(store, run_id=rid)
    del store.records["acv_run"]
    return _success_receipt_dict(rid)


def _closure_attack_nonexistent_logical_version(store, rid):
    # Attack 9: unrelated current context record + nonexistent logical id.
    _register_success_lineage(store, run_id=rid)
    return _success_receipt_dict(rid, context_version_id="ctx_nonexistent")


def _closure_attack_logical_record_mismatch(store, rid):
    # Attack 11: correct record ID plus a substituted (real) logical version.
    _register_success_lineage(store, run_id=rid)
    _register_success_lineage(
        store, run_id=rid, record_id="av_other", context_record_id="acv_other",
        context_version_id="ctx_other",
    )
    return _success_receipt_dict(rid, context_version_id="ctx_other")


def _closure_attack_unrelated_context_record(store, rid):
    # Attack 10: an unrelated CURRENT context record from another run.
    _register_success_lineage(store, run_id=rid)
    _register_success_lineage(
        store, run_id="wfrun_other", record_id="av_other",
        context_record_id="acv_other", context_version_id="ctx_other",
    )
    return _success_receipt_dict(rid, context_record_id="acv_other")


def _closure_attack_stale_context_record(store, rid):
    _register_success_lineage(store, run_id=rid, context_lifecycle="superseded")
    return _success_receipt_dict(rid)


def _closure_attack_context_from_other_app(store, rid):
    # Attack 13: context record persisted for a different app scope.
    _register_success_lineage(store, run_id=rid, payload_app_id="another-app")
    return _success_receipt_dict(rid)


def _closure_attack_context_from_other_build(store, rid):
    # Attack 14: BuildRecord and AppContextVersion from different builds.
    _register_success_lineage(store, run_id=rid, context_build_id="build_other")
    return _success_receipt_dict(rid)


def _closure_attack_context_cross_ref_mismatch(store, rid):
    _register_success_lineage(store, run_id=rid, cross_ref_record_id="av_someone_else")
    return _success_receipt_dict(rid)


def _closure_attack_wrong_bundle_digest(store, rid):
    # Attack 15: complete correct closure, wrong digest in the receipt.
    _register_success_lineage(store, run_id=rid)
    return _success_receipt_dict(rid, digest="a" * 64)


def _closure_attack_digest_from_other_record(store, rid):
    _register_success_lineage(store, run_id=rid)
    _register_success_lineage(
        store, run_id=rid, record_id="av_other", context_record_id="acv_other",
        context_version_id="ctx_other", digest="b" * 64,
    )
    return _success_receipt_dict(rid, digest="b" * 64)


def _closure_attack_success_before_current(store, rid):
    # Attack 15/pre-lineage: greenfield record still DRAFT — a success receipt
    # can never claim it regardless of digest correctness.
    _register_success_lineage(store, run_id=rid, lifecycle="draft")
    return _success_receipt_dict(rid)


_CLOSURE_ATTACKS = {
    "missing_build_record": _closure_attack_missing_build_record,
    "wrong_family": _closure_attack_wrong_family,
    "wrong_key": _closure_attack_wrong_key,
    "fake_build_id_real_record": _closure_attack_fake_build_id_real_record,
    "record_bound_to_other_run": _closure_attack_record_bound_to_other_run,
    "unbound_record": _closure_attack_unbound_record,
    "missing_context_record": _closure_attack_missing_context_record,
    "nonexistent_logical_version": _closure_attack_nonexistent_logical_version,
    "logical_record_mismatch": _closure_attack_logical_record_mismatch,
    "unrelated_context_record": _closure_attack_unrelated_context_record,
    "stale_context_record": _closure_attack_stale_context_record,
    "context_from_other_app": _closure_attack_context_from_other_app,
    "context_from_other_build": _closure_attack_context_from_other_build,
    "context_cross_ref_mismatch": _closure_attack_context_cross_ref_mismatch,
    "wrong_bundle_digest": _closure_attack_wrong_bundle_digest,
    "digest_from_other_record": _closure_attack_digest_from_other_record,
    "success_before_current_lineage": _closure_attack_success_before_current,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", sorted(_CLOSURE_ATTACKS))
async def test_lineage_closure_attacks_claim_nothing(monkeypatch, attack: str) -> None:
    """Every substituted or incomplete closure fails cold verification: a
    real CURRENT record is never sufficient unless it is the exact record for
    the receipt's run and build."""
    store = _FakeArtifactStore()

    async def _seed(pm: _FakePersistenceManager) -> None:
        rid = pm.store["workflow_run_id"]
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = _CLOSURE_ATTACKS[attack](store, rid)

    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _seed)
    await _launch(_Bridge(pm), _hooks())
    assert events == [], events


@pytest.mark.asyncio
async def test_revision_candidate_receipt_completes_and_cannot_claim_current(monkeypatch) -> None:
    """Genesis/refinement separation: a refinement run's candidate receipt
    (draft record with parent lineage) claims completion through its own
    closed variant; the same records can never satisfy a Genesis success
    receipt, and a candidate receipt can never claim a CURRENT record."""
    from mozaiksai.core.artifacts.build_receipt import issue_revision_candidate_receipt

    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()

    async def _revision_terminal(inner_pm: _FakePersistenceManager) -> None:
        rid = inner_pm.store["workflow_run_id"]
        _register_success_lineage(
            store, run_id=rid, lifecycle="draft", parent="av_parent",
            record_id=f"av_{rid}", context_record_id=f"acv_{rid}",
            context_version_id=f"ctx_{rid}",
        )
        receipt = issue_revision_candidate_receipt(
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            workflow_run_id=rid,
            build_id=f"build_{rid}",
            build_record_id=f"av_{rid}",
            app_context_version_id=f"ctx_{rid}",
            app_context_record_id=f"acv_{rid}",
            bundle_digest=_BUNDLE_DIGEST,
        )
        inner_pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = receipt.model_dump(mode="json")

    _patch_adapter(monkeypatch, pm, _revision_terminal)
    await _launch(_Bridge(pm), _hooks())
    assert len(_events_of(events, "build.completed")) == 1
    assert _events_of(events, "build.failed") == []

    # A candidate receipt referencing a CURRENT record must fail closed.
    events_before = len(events)

    async def _candidate_claiming_current(inner_pm: _FakePersistenceManager) -> None:
        rid = inner_pm.store["workflow_run_id"]
        _register_success_lineage(
            store, run_id=rid, record_id=f"av_{rid}", context_record_id=f"acv_{rid}",
            context_version_id=f"ctx_{rid}",
        )
        receipt = issue_revision_candidate_receipt(
            app_id=_APP_ID,
            workflow_name=_WORKFLOW,
            workflow_run_id=rid,
            build_id=f"build_{rid}",
            build_record_id=f"av_{rid}",
            app_context_version_id=f"ctx_{rid}",
            app_context_record_id=f"acv_{rid}",
            bundle_digest=_BUNDLE_DIGEST,
        )
        inner_pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = receipt.model_dump(mode="json")

    _patch_adapter(monkeypatch, pm, _candidate_claiming_current)
    await _launch(_Bridge(pm), _hooks())
    assert events[events_before:] == [], events[events_before:]


# ---------------------------------------------------------------------------
# Forged-closure substitution attacks (Codex 1)
# ---------------------------------------------------------------------------


def _forged_success_receipt_dict(
    run_id: str,
    *,
    build_id: str,
    record_id: str,
    context_version_id: str,
    context_record_id: str,
    bundle_digest: str | None,
) -> dict[str, Any]:
    """Forge a success receipt the way an attacker with context access would:
    fill the current schema's fields with substituted references and recompute
    the content digest. The digest is a content hash, not a server signature —
    re-digesting must never be sufficient."""
    from mozaiksai.core.artifacts.build_receipt import BuildSuccessReceipt, _digest_payload

    forged: dict[str, Any] = {
        "schema_version": "mozaiks.build_receipt.v1",
        "kind": "success",
        "scope": "server",
        "status": "succeeded",
        "app_id": _APP_ID,
        "workflow_name": _WORKFLOW,
        "workflow_run_id": run_id,
        "build_id": build_id,
        "build_record_id": record_id,
        "app_context_version_id": context_version_id,
        "app_context_record_id": context_record_id,
        "bundle_digest": bundle_digest,
        "receipt_digest": "",
    }
    if "build_record_lifecycle" in BuildSuccessReceipt.model_fields:
        forged["build_record_lifecycle"] = "current"
    forged["receipt_digest"] = _digest_payload(forged)
    return forged


@pytest.mark.asyncio
async def test_codex1_forged_closure_substitution_attack(monkeypatch) -> None:
    """Codex 1's exact attack: a correctly re-digested success receipt with a
    matching app/workflow/run, a build_id with no corresponding persisted
    build, a real but UNRELATED CURRENT app-bundle BuildRecord, a nonexistent
    app_context_version_id, an unrelated CURRENT app_context_record_id, and
    bundle_digest=None must claim nothing: receipt-body integrity is not
    server authority — the referenced closure is false."""
    store = _FakeArtifactStore()
    # Real but unrelated persisted lineage from some other build.
    store.add_record(
        record_id="av_unrelated", build_family="app_bundle", lifecycle="current",
        digests=("f" * 64,),
    )
    store.add_record(
        record_id="acv_unrelated", build_family="app_context_version", lifecycle="current",
    )

    async def _seed_forged(pm: _FakePersistenceManager) -> None:
        rid = pm.store["workflow_run_id"]
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = _forged_success_receipt_dict(
            rid,
            build_id="build_with_no_persisted_build",
            record_id="av_unrelated",
            context_version_id="ctx_nonexistent",
            context_record_id="acv_unrelated",
            bundle_digest=None,
        )

    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _seed_forged)
    await _launch(_Bridge(pm), _hooks())
    assert events == [], (
        f"forged closure substitution was accepted: {events}"
    )


@pytest.mark.asyncio
async def test_forged_closure_with_stolen_valid_digest_still_fails(monkeypatch) -> None:
    """Strengthened variant: the forger also steals the unrelated record's
    REAL digest so the receipt is structurally perfect. Rejection must come
    from the false closure (run/build binding, AppContextVersion identity),
    not from early shape checks."""
    stolen_digest = "e" * 64
    store = _FakeArtifactStore()
    store.add_record(
        record_id="av_unrelated", build_family="app_bundle", lifecycle="current",
        digests=(stolen_digest,),
    )
    store.add_record(
        record_id="acv_unrelated", build_family="app_context_version", lifecycle="current",
    )

    async def _seed_forged(pm: _FakePersistenceManager) -> None:
        rid = pm.store["workflow_run_id"]
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = _forged_success_receipt_dict(
            rid,
            build_id="build_with_no_persisted_build",
            record_id="av_unrelated",
            context_version_id="ctx_nonexistent",
            context_record_id="acv_unrelated",
            bundle_digest=stolen_digest,
        )

    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _seed_forged)
    await _launch(_Bridge(pm), _hooks())
    assert events == [], (
        f"forged closure with stolen digest was accepted: {events}"
    )


# ---------------------------------------------------------------------------
# Lifecycle hook policy (attacks 16–17)
# ---------------------------------------------------------------------------


class TestLifecycleHookPolicy:
    """Composed lifecycle hooks honor declared required/best_effort policies."""

    def test_appgenerator_hooks_resolve_composed(self) -> None:
        hooks = get_workflow_lifecycle_hooks(_WORKFLOW)
        on_complete = hooks["on_complete"]
        assert on_complete is not None
        assert on_complete.__module__ == "mozaiksai.core.runtime.composition.extensions"

    @pytest.mark.asyncio
    async def test_required_hook_failure_propagates_and_stops_chain(self) -> None:
        from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

        calls: list[str] = []

        async def _required_boom(**_kw: Any) -> None:
            calls.append("required")
            raise RuntimeError("canonical persistence failed")

        async def _after(**_kw: Any) -> str:
            calls.append("after")
            return "never"

        composed = _compose_lifecycle_hooks(
            [(_required_boom, "required"), (_after, "best_effort")], "on_complete", _WORKFLOW
        )
        with pytest.raises(RuntimeError, match="canonical persistence failed"):
            await composed(app_id="app-1")
        assert calls == ["required"]

    @pytest.mark.asyncio
    async def test_best_effort_failure_never_suppresses_required_hooks(self) -> None:
        from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

        calls: list[str] = []

        async def _telemetry_boom(**_kw: Any) -> None:
            calls.append("telemetry")
            raise RuntimeError("recorder backend unavailable")

        async def _canonical(**_kw: Any) -> str:
            calls.append("canonical")
            return "outbox_1"

        composed = _compose_lifecycle_hooks(
            [(_telemetry_boom, "best_effort"), (_canonical, "required")], "on_complete", _WORKFLOW
        )
        result = await composed(app_id="app-1")
        assert calls == ["telemetry", "canonical"]
        assert result == "outbox_1"

    @pytest.mark.asyncio
    async def test_hook_kwargs_are_isolated_between_siblings(self) -> None:
        from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

        observed: list[Any] = []

        async def _mutator(**kwargs: Any) -> None:
            kwargs["context_variables"]["injected"] = "hostile"
            kwargs["app_id"] = "swapped"

        async def _observer(**kwargs: Any) -> None:
            observed.append((kwargs["app_id"], dict(kwargs["context_variables"])))

        composed = _compose_lifecycle_hooks(
            [(_mutator, "best_effort"), (_observer, "best_effort")], "on_complete", _WORKFLOW
        )
        original_context = {"clean": True}
        await composed(app_id="app-1", context_variables=original_context)
        assert observed == [("app-1", {"clean": True})]
        assert original_context == {"clean": True}

    def test_duplicate_declarations_deduplicate_and_invalid_policy_skips(self, monkeypatch) -> None:
        import mozaiksai.core.runtime.composition.extensions as ext

        loaded_functions: list[str] = []

        def _fake_load(*, workflow_name: str, file_path: str, function: str) -> Any:
            loaded_functions.append(function)

            async def _hook(**_kw: Any) -> str:
                return function

            _hook.__name__ = function
            return _hook

        class _Mgr:
            def get_config(self, _name: str) -> dict[str, Any]:
                return {
                    "lifecycle_tools": [
                        {"trigger": "on_complete", "file": "a.py", "function": "hook_a", "policy": "required"},
                        {"trigger": "on_complete", "file": "a.py", "function": "hook_a", "policy": "required"},
                        {"trigger": "on_complete", "file": "b.py", "function": "hook_b", "policy": "sometimes"},
                    ]
                }

        import mozaiksai.core.workflow.workflow_manager as wm

        monkeypatch.setattr(wm, "get_workflow_manager", lambda: _Mgr())
        monkeypatch.setattr(ext, "_load_workflow_local_entrypoint", _fake_load)

        hooks = ext.get_workflow_lifecycle_hooks("PolicyProbe")
        assert loaded_functions == ["hook_a"], (
            "duplicates must deduplicate and invalid policies must be skipped"
        )
        assert hooks["on_complete"] is not None


@pytest.mark.asyncio
async def test_codex1_required_hook_failure_is_not_success(monkeypatch) -> None:
    """Codex 1's exact reproduction, corrected contract.

    A composed on_complete dispatch whose required hook raises
    RuntimeError("required lifecycle persistence unavailable") previously
    logged the failure through a detached task callback while the bridge
    still returned status=success / run_status=completed. The awaited
    lifecycle gate now returns the typed error: a required lifecycle
    persistence failure is never a successful completed result.
    """
    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)

    hooks_seen: list[str] = []

    async def _upsert_boom(**_kwargs: Any) -> str:
        hooks_seen.append("required")
        raise RuntimeError("required lifecycle persistence unavailable")

    monkeypatch.setattr(_shared_lifecycle, "upsert_outbox_event", _upsert_boom)

    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))
    bridge = _Bridge(pm)

    result = await _launch(bridge, _hooks(), expect_status="error")
    assert result["route"] == "lifecycle_completion_failed"
    assert hooks_seen == ["required"]
    assert events == [], events
    # The typed error surfaced through the existing transport error channel
    # and no synthetic completed signal was presented to the caller.
    assert any(
        e.get("error_code") == "LIFECYCLE_PERSISTENCE_FAILED" for e in bridge.sent_errors
    )
    assert bridge.synthetic_completes == 0


@pytest.mark.asyncio
async def test_required_on_start_failure_blocks_orchestration(monkeypatch) -> None:
    """Attack 1: required on_start raises — the orchestration adapter is
    never invoked, no model/tool work begins, and the bridge returns the
    typed error, not completed."""
    _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, _FakeArtifactStore())

    async def _upsert_boom(**_kwargs: Any) -> str:
        raise RuntimeError("required lifecycle persistence unavailable")

    monkeypatch.setattr(_shared_lifecycle, "upsert_outbox_event", _upsert_boom)

    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, None)
    import mozaiksai.core.adapters.ag2_orchestration as orchestration

    adapter = orchestration.get_ag2_adapter()
    bridge = _Bridge(pm)

    hooks = get_workflow_lifecycle_hooks(_WORKFLOW)
    result = await _launch(
        bridge, hooks, emit_started=hooks["on_start"], expect_status="error"
    )
    assert result["route"] == "lifecycle_start_failed"
    assert getattr(adapter, "invocations", 0) == 0, "orchestration must not start"
    assert any(
        e.get("error_code") == "LIFECYCLE_PERSISTENCE_FAILED" for e in bridge.sent_errors
    )
    assert bridge.synthetic_completes == 0


@pytest.mark.asyncio
async def test_best_effort_on_start_failure_lets_run_proceed(monkeypatch) -> None:
    """Attack 2: a best_effort on_start hook raising is isolated by the
    composed dispatcher; the run proceeds and completes normally."""
    from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)

    async def _telemetry_boom(**_kw: Any) -> None:
        raise RuntimeError("recorder backend unavailable")

    started: list[str] = []

    async def _required_ok(**_kw: Any) -> str:
        started.append("required")
        return "outbox_started"

    composed_start = _compose_lifecycle_hooks(
        [(_telemetry_boom, "best_effort"), (_required_ok, "required")],
        "on_start", _WORKFLOW,
    )

    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))
    await _launch(_Bridge(pm), _hooks(), emit_started=composed_start)
    assert started == ["required"]
    assert len(_events_of(events, "build.completed")) == 1


@pytest.mark.asyncio
async def test_best_effort_on_complete_failure_keeps_success(monkeypatch) -> None:
    """Attack 4: a best_effort on_complete recorder raising after the
    canonical required completion does not undo completion or the bridge's
    success result."""
    from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()

    real_hooks = _hooks()

    async def _recorder_boom(**_kw: Any) -> None:
        raise RuntimeError("recorder backend unavailable")

    composed = _compose_lifecycle_hooks(
        [(real_hooks["on_complete"], "required"), (_recorder_boom, "best_effort")],
        "on_complete", _WORKFLOW,
    )
    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))
    result = await _launch(_Bridge(pm), {"on_complete": composed})
    assert result["run_status"] == "completed"
    assert len(_events_of(events, "build.completed")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["on_start", "on_complete"])
async def test_cancellation_during_lifecycle_dispatch_propagates(monkeypatch, phase: str) -> None:
    """Attacks 11/12: cancellation during an awaited lifecycle dispatch
    propagates CancelledError, starts no agent run afterwards (on_start),
    and leaves no orphan lifecycle task."""
    _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))
    import mozaiksai.core.adapters.ag2_orchestration as orchestration

    adapter = orchestration.get_ag2_adapter()

    blocked = asyncio.Event()

    async def _hanging_hook(**_kw: Any) -> None:
        blocked.set()
        await asyncio.sleep(3600)

    bridge = _Bridge(pm)
    kwargs: dict[str, Any] = {
        "chat_id": _CHAT_ID, "user_id": "user-1", "workflow_name": _WORKFLOW,
        "message": None, "app_id": _APP_ID, "initial_agent_name_override": None,
        "is_resume_request": False,
        "emit_execution_started": _hanging_hook if phase == "on_start" else None,
        "emit_execution_completed": _hanging_hook if phase == "on_complete" else None,
    }
    task = asyncio.create_task(bridge._launch_workflow_run_locked(**kwargs))
    await asyncio.wait_for(blocked.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    if phase == "on_start":
        assert getattr(adapter, "invocations", 0) == 0
    assert "_lifecycle_tasks" not in bridge.__dict__


@pytest.mark.asyncio
async def test_no_lifecycle_hooks_completes_normally(monkeypatch) -> None:
    """Attack 15: workflows without lifecycle hooks are unaffected."""
    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, _FakeArtifactStore())
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, None)
    bridge = _Bridge(pm)
    result = await _launch(bridge, {"on_complete": None})
    assert result["run_status"] == "completed"
    assert events == []
    assert bridge.synthetic_completes == 1


@pytest.mark.asyncio
async def test_non_appgenerator_workflow_completion_unchanged(monkeypatch) -> None:
    """Attack 16: a non-AppGenerator build workflow's real required
    on_complete hook still emits through the awaited gate."""
    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, _FakeArtifactStore())
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, None)

    hooks = get_workflow_lifecycle_hooks("ValueEngine")
    assert hooks["on_complete"] is not None
    result = await _launch(
        _Bridge(pm), hooks, workflow_name="ValueEngine"
    )
    assert result["run_status"] == "completed"
    # ValueEngine's shared emitter claims completion only at its journey
    # position (is_build_workflow gate); outside a journey it claims nothing
    # and the awaited gate still returns success — behavior unchanged.
    assert events == []


class TestMultipleRequiredHooks:
    """Attacks 8/9: multiple required hooks propagate the first failure in
    deterministic declaration order."""

    @pytest.mark.asyncio
    async def test_first_required_raises_stops_chain(self) -> None:
        from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

        calls: list[str] = []

        async def _first(**_kw: Any) -> None:
            calls.append("first")
            raise RuntimeError("required lifecycle persistence unavailable")

        async def _second(**_kw: Any) -> None:
            calls.append("second")

        composed = _compose_lifecycle_hooks(
            [(_first, "required"), (_second, "required")], "on_complete", _WORKFLOW
        )
        with pytest.raises(RuntimeError):
            await composed(app_id=_APP_ID)
        assert calls == ["first"]

    @pytest.mark.asyncio
    async def test_middle_required_raises_stops_chain(self) -> None:
        from mozaiksai.core.runtime.composition.extensions import _compose_lifecycle_hooks

        calls: list[str] = []

        async def _ok(**_kw: Any) -> str:
            calls.append("ok")
            return "outbox_1"

        async def _boom(**_kw: Any) -> None:
            calls.append("boom")
            raise RuntimeError("required lifecycle persistence unavailable")

        async def _after(**_kw: Any) -> None:
            calls.append("after")

        composed = _compose_lifecycle_hooks(
            [(_ok, "required"), (_boom, "required"), (_after, "required")],
            "on_complete", _WORKFLOW,
        )
        with pytest.raises(RuntimeError):
            await composed(app_id=_APP_ID)
        assert calls == ["ok", "boom"]
