"""Unit tests for the chat execution lease (distributed same-chat exclusion).

Covers mode resolution, canonical lock identity, local-mode serialization,
fail-closed behavior when the lock authority is unavailable, the lease-loss
write guard, and the workflow-bridge rejection paths.

Cross-instance exclusion against real MongoDB is proven separately in
tests/test_chat_execution_lease_real_mongo.py.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mozaiksai.core.runtime.persistence import distributed_lock as dl
from tests.import_utils import import_module_directly

_bridge_mod = import_module_directly("mozaiksai.core.transport.workflow_bridge")
_ag2_mod = import_module_directly("mozaiksai.core.adapters.ag2_orchestration")

WorkflowBridgeMixin = _bridge_mod.WorkflowBridgeMixin


@pytest.fixture(autouse=True)
def _clean_lock_state(monkeypatch):
    monkeypatch.delenv(dl.CHAT_LOCK_MODE_ENV, raising=False)
    dl.reset_chat_lock_state()
    yield
    dl.reset_chat_lock_state()


# ---------------------------------------------------------------------------
# Mode resolution — the single-process boundary is explicit and testable
# ---------------------------------------------------------------------------

def test_unconfigured_process_defaults_to_local_mode() -> None:
    assert dl.get_chat_lock_mode() is dl.ChatLockMode.LOCAL


def test_env_override_selects_required_mode(monkeypatch) -> None:
    monkeypatch.setenv(dl.CHAT_LOCK_MODE_ENV, "required")
    assert dl.get_chat_lock_mode() is dl.ChatLockMode.REQUIRED


def test_configure_resolves_required_when_database_persistence_enabled(monkeypatch) -> None:
    monkeypatch.setenv("MONGO_URI", "mongodb://example.invalid:27017")
    assert dl.configure_chat_lock() is dl.ChatLockMode.REQUIRED
    assert dl.get_chat_lock_mode() is dl.ChatLockMode.REQUIRED


def test_configure_resolves_local_without_database_persistence(monkeypatch) -> None:
    for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL", "ENV", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    assert dl.configure_chat_lock() is dl.ChatLockMode.LOCAL


def test_production_environment_configures_required_mode(monkeypatch) -> None:
    for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENV", "production")
    assert dl.configure_chat_lock() is dl.ChatLockMode.REQUIRED


# ---------------------------------------------------------------------------
# Canonical lock identity — tenant-scoped, server-owned normalization
# ---------------------------------------------------------------------------

def test_chat_lock_resource_is_tenant_scoped() -> None:
    assert dl.chat_lock_resource("app-1", "chat-9") == "chat:app-1:chat-9"
    assert dl.chat_lock_resource("app-2", "chat-9") != dl.chat_lock_resource("app-1", "chat-9")


def test_chat_lock_resource_normalizes_invalid_app_ids() -> None:
    assert dl.chat_lock_resource(None, "chat-9") == "chat:__invalid__:chat-9"
    assert dl.chat_lock_resource("  ", "chat-9") == "chat:__invalid__:chat-9"
    assert dl.chat_lock_resource(" app-1 ", "chat-9") == "chat:app-1:chat-9"


# ---------------------------------------------------------------------------
# Local mode — explicit single-process serialization, not distributed safety
# ---------------------------------------------------------------------------

async def test_local_mode_serializes_same_chat() -> None:
    order: list[str] = []

    async def hold(tag: str, delay: float) -> None:
        async with dl.chat_execution_lease(app_id="app-1", chat_id="chat-1"):
            order.append(f"{tag}:enter")
            await asyncio.sleep(delay)
            order.append(f"{tag}:exit")

    await asyncio.gather(hold("a", 0.05), hold("b", 0.0))
    # Whoever entered first must exit before the other enters.
    assert order[0].endswith(":enter") and order[1] == order[0].replace(":enter", ":exit")


async def test_local_mode_rejects_busy_after_budget(monkeypatch) -> None:
    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "1")
    monkeypatch.setenv("DISTRIBUTED_LOCK_RETRY_DELAY", "0.01")

    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with dl.chat_execution_lease(app_id="app-1", chat_id="chat-busy"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    with pytest.raises(dl.LockAcquisitionError):
        async with dl.chat_execution_lease(app_id="app-1", chat_id="chat-busy"):
            pass
    release.set()
    await task


async def test_local_mode_allows_distinct_chats_and_tenants_concurrently() -> None:
    async def enter(app_id: str, chat_id: str, gate: asyncio.Event) -> None:
        async with dl.chat_execution_lease(app_id=app_id, chat_id=chat_id):
            await gate.wait()

    gate = asyncio.Event()
    tasks = [
        asyncio.create_task(enter("app-1", "chat-1", gate)),
        asyncio.create_task(enter("app-1", "chat-2", gate)),
        asyncio.create_task(enter("app-2", "chat-1", gate)),
    ]
    # All three must be inside their lease simultaneously.
    await asyncio.sleep(0.1)
    assert all(not t.done() for t in tasks)
    gate.set()
    await asyncio.gather(*tasks)


async def test_local_mode_releases_on_exception() -> None:
    with pytest.raises(RuntimeError):
        async with dl.chat_execution_lease(app_id="app-1", chat_id="chat-exc"):
            raise RuntimeError("boom")
    # Lease is free again.
    async with dl.chat_execution_lease(app_id="app-1", chat_id="chat-exc"):
        pass


# ---------------------------------------------------------------------------
# Required mode — fail closed when the authority is unavailable
# ---------------------------------------------------------------------------

async def test_required_mode_fails_closed_when_authority_unavailable(monkeypatch) -> None:
    dl.configure_chat_lock(dl.ChatLockMode.REQUIRED)
    monkeypatch.setattr(dl, "_get_lock_collection", lambda: None)
    with pytest.raises(dl.ChatLockAuthorityUnavailableError):
        async with dl.chat_execution_lease(app_id="app-1", chat_id="chat-1"):
            pytest.fail("must not enter the protected section without a lease")


class _IndexlessCollection:
    """The unique index cannot be created — exclusion is unprovable."""

    async def create_index(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("index creation refused")

    async def index_information(self):
        return {}


async def test_required_mode_fails_closed_without_unique_index(monkeypatch) -> None:
    """Without the unique resource index the insert-based acquire is not
    atomic (two holders can both insert), so acquisition must fail closed
    rather than degrade to a cosmetic lock."""
    dl.configure_chat_lock(dl.ChatLockMode.REQUIRED)
    monkeypatch.setattr(dl, "_get_lock_collection", lambda: _IndexlessCollection())
    with pytest.raises(dl.ChatLockAuthorityUnavailableError):
        async with dl.chat_execution_lease(app_id="app-1", chat_id="chat-1"):
            pytest.fail("must not enter the protected section without index-backed atomicity")


# ---------------------------------------------------------------------------
# Lease-loss write guard
# ---------------------------------------------------------------------------

def _register_lease(resource: str, *, lost: bool) -> dl.ChatLease:
    lease = dl.ChatLease(
        resource=resource,
        holder_id="holder-1",
        mode=dl.ChatLockMode.REQUIRED,
        collection=None,
        ttl_seconds=60,
    )
    if lost:
        lease._mark_lost("test")
    dl._process_leases[resource] = lease
    return lease


def test_assert_chat_mutable_noop_without_registered_lease() -> None:
    dl.assert_chat_mutable(app_id="app-1", chat_id="chat-1")


def test_assert_chat_mutable_noop_while_lease_held() -> None:
    _register_lease(dl.chat_lock_resource("app-1", "chat-1"), lost=False)
    dl.assert_chat_mutable(app_id="app-1", chat_id="chat-1")


def test_assert_chat_mutable_raises_after_confirmed_lease_loss() -> None:
    _register_lease(dl.chat_lock_resource("app-1", "chat-1"), lost=True)
    with pytest.raises(dl.ChatLeaseLostError):
        dl.assert_chat_mutable(app_id="app-1", chat_id="chat-1")
    # Other chats stay unaffected.
    dl.assert_chat_mutable(app_id="app-1", chat_id="chat-2")
    dl.assert_chat_mutable(app_id="app-2", chat_id="chat-1")


class _FakeRenewCollection:
    """find_one_and_update returns None — the lease is gone or stolen."""

    def __init__(self) -> None:
        self.calls = 0

    async def find_one_and_update(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls += 1
        return None


async def test_renewal_miss_marks_lease_lost() -> None:
    lease = dl.ChatLease(
        resource="chat:app-1:chat-1",
        holder_id="holder-1",
        mode=dl.ChatLockMode.REQUIRED,
        collection=_FakeRenewCollection(),
        ttl_seconds=60,
    )
    assert not lease.lost
    await lease._renew_once()
    assert lease.lost


# ---------------------------------------------------------------------------
# Workflow bridge rejection paths — no mutation on busy / unavailable
# ---------------------------------------------------------------------------

class _FakePersistenceManager:
    def __init__(self) -> None:
        self.pending_lookups: list[dict[str, str]] = []
        self.pending_clears: list[dict[str, str]] = []
        self.run_user_messages: list[dict[str, object]] = []

    async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
        self.pending_lookups.append(kwargs)
        return None

    async def clear_pending_input_request(self, **kwargs):  # noqa: ANN003
        self.pending_clears.append(kwargs)

    async def append_run_user_message(self, **kwargs):  # noqa: ANN003
        self.run_user_messages.append(kwargs)


class _FakeAdapter:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.run_requests: list[object] = []
        self.resume_requests: list[object] = []

    async def run(self, request):  # noqa: ANN001
        self.run_requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        return SimpleNamespace(status=SimpleNamespace(value="completed"))

    async def resume(self, request):  # noqa: ANN001
        self.resume_requests.append(request)
        return SimpleNamespace(status=SimpleNamespace(value="completed"))


class _DummyTransport(WorkflowBridgeMixin):
    def __init__(self, persistence_manager: _FakePersistenceManager) -> None:
        self._input_request_registries = {}
        self._workflow_spawn_semaphore = None
        self._background_tasks = {}
        self.connections = {}
        self._derived_context_managers = {}
        self._persistence_manager = persistence_manager
        self.persisted_messages: list[dict[str, str | None]] = []
        self.errors: list[dict[str, object]] = []
        self._live_ag2_workflow_runs: dict[str, object] = {}

    def _get_or_create_persistence_manager(self):
        return self._persistence_manager

    async def process_incoming_user_message(self, **kwargs) -> None:  # noqa: ANN003
        self.persisted_messages.append(kwargs)

    async def send_error(
        self,
        error_message: str,
        error_code: str,
        chat_id: str,
        extra_data: dict | None = None,
    ) -> None:
        self.errors.append({"error_code": error_code, "chat_id": chat_id})

    async def send_event_to_ui(self, event, chat_id=None) -> None:  # noqa: ANN001
        pass

    async def _apply_user_text_context_updates(self, **kwargs):  # noqa: ANN003
        return {}

    def get_live_ag2_workflow_run(self, chat_id: str):
        return self._live_ag2_workflow_runs.get(chat_id)


async def test_bridge_busy_loser_performs_no_mutation(monkeypatch) -> None:
    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "1")
    monkeypatch.setenv("DISTRIBUTED_LOCK_RETRY_DELAY", "0.01")
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _name: {})

    winner_pm, loser_pm = _FakePersistenceManager(), _FakePersistenceManager()
    winner, loser = _DummyTransport(winner_pm), _DummyTransport(loser_pm)
    adapter = _FakeAdapter(delay=0.3)
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)

    winner_task = asyncio.create_task(
        winner.handle_user_input_from_api(
            chat_id="chat-1", user_id="u1", workflow_name="AppGenerator",
            message="go", app_id="app-1",
        )
    )
    await asyncio.sleep(0.05)  # winner is inside the lease, adapter running
    loser_result = await loser.handle_user_input_from_api(
        chat_id="chat-1", user_id="u2", workflow_name="AppGenerator",
        message="me too", app_id="app-1",
    )
    winner_result = await winner_task

    assert winner_result["status"] == "success"
    assert loser_result["status"] == "busy"
    assert loser_result["route"] == "chat_lock_busy"
    assert loser.errors == [{"error_code": "CHAT_LOCK_BUSY", "chat_id": "chat-1"}]
    # The loser performed no session, workflow, or WAL mutation.
    assert loser_pm.pending_lookups == []
    assert loser_pm.pending_clears == []
    assert loser_pm.run_user_messages == []
    assert loser.persisted_messages == []
    assert len(adapter.run_requests) == 1


async def test_bridge_fails_closed_when_authority_unavailable(monkeypatch) -> None:
    dl.configure_chat_lock(dl.ChatLockMode.REQUIRED)
    monkeypatch.setattr(dl, "_get_lock_collection", lambda: None)
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _name: {})

    pm = _FakePersistenceManager()
    transport = _DummyTransport(pm)
    adapter = _FakeAdapter()
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)

    result = await transport.handle_user_input_from_api(
        chat_id="chat-1", user_id="u1", workflow_name="AppGenerator",
        message="go", app_id="app-1",
    )

    assert result["status"] == "error"
    assert result["route"] == "chat_lock_unavailable"
    assert transport.errors == [{"error_code": "CHAT_LOCK_UNAVAILABLE", "chat_id": "chat-1"}]
    # Rejected before any session/WAL mutation or orchestration launch.
    assert pm.pending_lookups == []
    assert pm.pending_clears == []
    assert pm.run_user_messages == []
    assert transport.persisted_messages == []
    assert adapter.run_requests == []
    assert adapter.resume_requests == []
