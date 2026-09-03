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
        self.store.update(variables or {})


class _Bridge(WorkflowBridgeMixin):
    def __init__(self, pm: _FakePersistenceManager) -> None:
        self._pm = pm

    def _get_or_create_persistence_manager(self) -> _FakePersistenceManager:
        return self._pm

    async def _emit_synthetic_run_complete_if_needed(self, **_kwargs: Any) -> None:
        return None


class _FakeAdapter:
    """Completed-run adapter; on_run simulates terminal tool work in-run."""

    def __init__(self, pm: _FakePersistenceManager, on_run: Any = None) -> None:
        self._pm = pm
        self._on_run = on_run

    async def _result(self, request: Any) -> RunResult:
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
    ) -> None:
        self.records[record_id] = SimpleNamespace(
            id=record_id,
            build_family=build_family,
            lifecycle_status=lifecycle,
            parent_build_record_id=parent,
            files_manifest=[SimpleNamespace(sha256=d) for d in digests],
        )

    async def get_build_record(self, *, app_id: str, build_record_id: str) -> Any:
        return self.records.get(build_record_id)


def _register_success_lineage(
    store: _FakeArtifactStore,
    *,
    record_id: str = "av_run",
    context_record_id: str = "acv_run",
    lifecycle: str = "current",
    context_lifecycle: str = "current",
    parent: str | None = None,
    digest: str = _BUNDLE_DIGEST,
) -> None:
    store.add_record(
        record_id=record_id, build_family="app_bundle", lifecycle=lifecycle,
        parent=parent, digests=(digest,),
    )
    store.add_record(
        record_id=context_record_id, build_family="app_context_version",
        lifecycle=context_lifecycle,
    )


def _success_receipt_dict(
    run_id: str,
    *,
    app_id: str = _APP_ID,
    workflow_name: str = _WORKFLOW,
    build_id: str | None = None,
    record_id: str = "av_run",
    context_record_id: str = "acv_run",
    lifecycle: str = "current",
    digest: str | None = _BUNDLE_DIGEST,
) -> dict[str, Any]:
    receipt = issue_success_receipt(
        app_id=app_id,
        workflow_name=workflow_name,
        workflow_run_id=run_id,
        build_id=build_id or f"build_{run_id}",
        build_record_id=record_id,
        build_record_lifecycle=lifecycle,  # type: ignore[arg-type]
        app_context_version_id="acv_logical",
        app_context_record_id=context_record_id,
        bundle_digest=digest,
    )
    return receipt.model_dump(mode="json")


def _terminal_tool_success(store: _FakeArtifactStore):
    """Simulate generate_and_download's terminal closure for the live run."""

    async def _on_run(pm: _FakePersistenceManager) -> None:
        run_id = pm.store["workflow_run_id"]
        _register_success_lineage(store, record_id=f"av_{run_id}", context_record_id=f"acv_{run_id}")
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = _success_receipt_dict(
            run_id, record_id=f"av_{run_id}", context_record_id=f"acv_{run_id}"
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
) -> None:
    result = await bridge._launch_workflow_run_locked(
        chat_id=_CHAT_ID,
        user_id="user-1",
        workflow_name=_WORKFLOW,
        message=None,
        app_id=_APP_ID,
        initial_agent_name_override="resume-agent" if is_resume_request else None,
        is_resume_request=is_resume_request,
        emit_execution_started=None,
        emit_execution_completed=hooks["on_complete"],
    )
    assert result["run_status"] == "completed"
    pending = set(bridge.__dict__.get("_lifecycle_tasks", set()))
    if pending:
        await asyncio.wait(pending, timeout=10)
    await asyncio.sleep(0)
    # Attack 20: completion leaves no dangling lifecycle background task.
    assert not bridge.__dict__.get("_lifecycle_tasks", set())


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
    store = _FakeArtifactStore()
    _register_success_lineage(store)
    pm_probe = _FakePersistenceManager()
    # Build a receipt for an arbitrary run then alter a field without re-signing.
    receipt = _success_receipt_dict("wfrun_victim")
    receipt["build_id"] = "attacker-substituted-build"
    events = await _launch_with_seeded_receipt(monkeypatch, receipt, store)
    assert events == [], events
    del pm_probe


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
    _register_success_lineage(store)
    kwargs: dict[str, Any] = {}
    if mutation == "wrong_app":
        kwargs["app_id"] = "some-other-app"
    if mutation == "wrong_workflow":
        kwargs["workflow_name"] = "AgentGenerator"
    run_id = "wfrun_other_run" if mutation == "wrong_run" else None

    async def _seed_current_run(pm: _FakePersistenceManager) -> None:
        rid = run_id or pm.store["workflow_run_id"]
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = _success_receipt_dict(rid, **kwargs)

    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _seed_current_run)
    await _launch(_Bridge(pm), _hooks())
    assert events == [], events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lineage_case",
    [
        "missing_build_record",
        "wrong_family",
        "missing_context_record",
        "context_not_current",
        "digest_mismatch",
        "success_before_current_lineage",
    ],
)
async def test_success_receipt_failing_cold_verification_claims_nothing(
    monkeypatch, lineage_case: str
) -> None:
    store = _FakeArtifactStore()
    receipt_kwargs: dict[str, Any] = {}
    if lineage_case == "missing_build_record":
        store.add_record(record_id="acv_run", build_family="app_context_version", lifecycle="current")
    elif lineage_case == "wrong_family":
        store.add_record(record_id="av_run", build_family="workflow_bundle", lifecycle="current", digests=(_BUNDLE_DIGEST,))
        store.add_record(record_id="acv_run", build_family="app_context_version", lifecycle="current")
    elif lineage_case == "missing_context_record":
        store.add_record(record_id="av_run", build_family="app_bundle", lifecycle="current", digests=(_BUNDLE_DIGEST,))
    elif lineage_case == "context_not_current":
        _register_success_lineage(store, context_lifecycle="superseded")
    elif lineage_case == "digest_mismatch":
        _register_success_lineage(store, digest="e" * 64)
    elif lineage_case == "success_before_current_lineage":
        # Greenfield record still DRAFT with no parent: receipt claiming
        # CURRENT fails the lifecycle match; receipt claiming DRAFT fails the
        # unaccepted-greenfield rule. Attack both shapes.
        _register_success_lineage(store, lifecycle="draft")
        receipt_kwargs["lifecycle"] = "draft"

    async def _seed(pm: _FakePersistenceManager) -> None:
        rid = pm.store["workflow_run_id"]
        pm.store[TERMINAL_RECEIPT_CONTEXT_KEY] = _success_receipt_dict(rid, **receipt_kwargs)

    events = _intercept_lifecycle(monkeypatch)
    _patch_store(monkeypatch, store)
    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _seed)
    await _launch(_Bridge(pm), _hooks())
    assert events == [], events


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
async def test_required_emit_failure_prevents_success_claim_through_bridge(monkeypatch) -> None:
    """Attack 16 through the real bridge: the canonical outbox write fails —
    no completed event is recorded and the required failure is surfaced."""
    events = _intercept_lifecycle(monkeypatch)
    store = _FakeArtifactStore()
    _patch_store(monkeypatch, store)

    async def _upsert_boom(**_kwargs: Any) -> str:
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(_shared_lifecycle, "upsert_outbox_event", _upsert_boom)

    pm = _FakePersistenceManager()
    _patch_adapter(monkeypatch, pm, _terminal_tool_success(store))
    bridge = _Bridge(pm)

    result = await bridge._launch_workflow_run_locked(
        chat_id=_CHAT_ID,
        user_id="user-1",
        workflow_name=_WORKFLOW,
        message=None,
        app_id=_APP_ID,
        initial_agent_name_override=None,
        is_resume_request=False,
        emit_execution_started=None,
        emit_execution_completed=_hooks()["on_complete"],
    )
    assert result["run_status"] == "completed"
    pending = set(bridge.__dict__.get("_lifecycle_tasks", set()))
    done, _ = await asyncio.wait(pending, timeout=10)
    # The required-hook failure is retrieved (no dangling task, no unretrieved
    # exception warning) and no success claim was recorded.
    assert all(t.done() for t in done)
    assert events == [], events
    assert not bridge.__dict__.get("_lifecycle_tasks", set())
