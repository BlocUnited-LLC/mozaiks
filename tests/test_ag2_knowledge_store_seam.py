"""Tests for the AG2 KnowledgeStore injection seam.

Verifies:
1. No knowledge_store supplied → MemoryKnowledgeStore is used (current behavior).
2. A custom AG2-compatible KnowledgeStore can be supplied.
3. The supplied store reaches Hub.open() — not a different store.
4. Two runs with distinct store instances do not share AG2 workflow memory.
5. A single store instance CAN be shared across runs when the caller supplies it.
6. AG2NetworkRunnerRequest public contract is backward-compatible (no new required args).
7. run_workflow_orchestration accepts knowledge_store kwarg without error.
8. The knowledge_store field is None by default on the request.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozaiksai.core.adapters.ag2_network_runner import (
    AG2NetworkRunner,
    AG2NetworkRunnerRequest,
)

# ---------------------------------------------------------------------------
# Minimal AG2-compatible fake KnowledgeStore
# ---------------------------------------------------------------------------

class FakeKnowledgeStore:
    """Duck-typed KnowledgeStore (satisfies ag2.knowledge.KnowledgeStore Protocol)."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self._data: dict[str, str] = {}
        self.calls: list[str] = []

    async def read(self, path: str) -> str | None:
        self.calls.append(f"read:{path}")
        return self._data.get(path)

    async def write(self, path: str, content: str) -> None:
        self.calls.append(f"write:{path}")
        self._data[path] = content

    async def list(self, path: str = "/") -> list[str]:
        self.calls.append(f"list:{path}")
        return []

    async def delete(self, path: str) -> None:
        self.calls.append(f"delete:{path}")
        self._data.pop(path, None)

    async def exists(self, path: str) -> bool:
        return path in self._data

    async def append(self, path: str, content: str) -> int:
        existing = self._data.get(path, "")
        offset = len(existing.encode())
        self._data[path] = existing + content
        return offset

    async def read_range(self, path: str, start: int, end: int | None = None) -> str:
        content = self._data.get(path, "")
        data = content.encode()[start:end]
        return data.decode()

    async def on_change(self, path: str, callback: Any) -> Any:
        return SimpleNamespace(close=AsyncMock())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(**overrides: Any) -> AG2NetworkRunnerRequest:
    """Return a minimal AG2NetworkRunnerRequest with sensible defaults."""
    defaults: dict[str, Any] = dict(
        workflow_name="TestWorkflow",
        chat_id="chat-1",
        app_id="app-1",
        agents={"AgentA": MagicMock()},
        transition_rules=[],
        initial_agent_name="AgentA",
        initial_message="hello",
    )
    defaults.update(overrides)
    return AG2NetworkRunnerRequest(**defaults)


# ---------------------------------------------------------------------------
# 1. Default: knowledge_store is None → MemoryKnowledgeStore used
# ---------------------------------------------------------------------------

class TestDefaultBehavior:
    def test_knowledge_store_defaults_to_none(self) -> None:
        req = _make_request()
        assert req.knowledge_store is None

    def test_existing_required_fields_unchanged(self) -> None:
        req = _make_request()
        assert req.workflow_name == "TestWorkflow"
        assert req.chat_id == "chat-1"
        assert req.app_id == "app-1"
        assert req.initial_agent_name == "AgentA"
        assert req.initial_message == "hello"

    def test_request_constructable_without_knowledge_store(self) -> None:
        # Proves backward compatibility — no new required arguments
        req = AG2NetworkRunnerRequest(
            workflow_name="W",
            chat_id="c",
            app_id="a",
            agents={"A": MagicMock()},
            transition_rules=[],
            initial_agent_name="A",
            initial_message="hi",
        )
        assert req.knowledge_store is None

    @pytest.mark.asyncio
    async def test_no_store_uses_memory_store(self) -> None:
        """When knowledge_store is None, Hub.open receives a fresh MemoryKnowledgeStore."""
        from ag2.knowledge import MemoryKnowledgeStore

        req = _make_request()

        captured_store: list[Any] = []

        async def fake_hub_open(store: Any, **kwargs: Any) -> Any:
            captured_store.append(store)
            # Return a minimal fake hub that won't try to run agents
            hub = MagicMock()
            hub.close = AsyncMock()
            hub.register_listener = MagicMock()
            hub.read_wal = AsyncMock(return_value=[])
            hub.adapter_state = MagicMock(return_value=SimpleNamespace(context_vars={}))
            return hub

        with patch("mozaiksai.core.adapters.ag2_network_runner.Hub.open", fake_hub_open):
            runner = AG2NetworkRunner()
            # The run will fail after Hub.open because agents aren't real AG2 agents,
            # but we only need to confirm Hub.open was called with a MemoryKnowledgeStore.
            await runner.run(req)

        assert len(captured_store) == 1
        assert isinstance(captured_store[0], MemoryKnowledgeStore)


# ---------------------------------------------------------------------------
# 2. Custom store is supplied → reaches Hub.open
# ---------------------------------------------------------------------------

class TestCustomStoreReachesHub:
    @pytest.mark.asyncio
    async def test_supplied_store_passed_to_hub_open(self) -> None:
        custom_store = FakeKnowledgeStore(name="custom")
        req = _make_request(knowledge_store=custom_store)

        captured_store: list[Any] = []

        async def fake_hub_open(store: Any, **kwargs: Any) -> Any:
            captured_store.append(store)
            hub = MagicMock()
            hub.close = AsyncMock()
            hub.register_listener = MagicMock()
            hub.read_wal = AsyncMock(return_value=[])
            hub.adapter_state = MagicMock(return_value=SimpleNamespace(context_vars={}))
            return hub

        with patch("mozaiksai.core.adapters.ag2_network_runner.Hub.open", fake_hub_open):
            await AG2NetworkRunner().run(req)

        assert len(captured_store) == 1
        assert captured_store[0] is custom_store, (
            "The exact supplied store instance must be forwarded to Hub.open, not a copy."
        )

    @pytest.mark.asyncio
    async def test_no_memory_store_created_when_custom_store_supplied(self) -> None:
        """When a custom store is supplied, MemoryKnowledgeStore() must not be constructed."""
        custom_store = FakeKnowledgeStore(name="custom")
        req = _make_request(knowledge_store=custom_store)

        memory_store_calls: list[Any] = []
        OriginalMemory = __import__(
            "ag2.knowledge", fromlist=["MemoryKnowledgeStore"]
        ).MemoryKnowledgeStore

        class TrackingMemoryStore(OriginalMemory):
            def __init__(self) -> None:
                memory_store_calls.append(True)
                super().__init__()

        async def fake_hub_open(store: Any, **kwargs: Any) -> Any:
            hub = MagicMock()
            hub.close = AsyncMock()
            hub.register_listener = MagicMock()
            hub.read_wal = AsyncMock(return_value=[])
            hub.adapter_state = MagicMock(return_value=SimpleNamespace(context_vars={}))
            return hub

        with (
            patch("mozaiksai.core.adapters.ag2_network_runner.Hub.open", fake_hub_open),
            patch(
                "mozaiksai.core.adapters.ag2_network_runner.MemoryKnowledgeStore",
                TrackingMemoryStore,
            ),
        ):
            await AG2NetworkRunner().run(req)

        assert memory_store_calls == [], (
            "MemoryKnowledgeStore() must not be constructed when a custom store is supplied."
        )


# ---------------------------------------------------------------------------
# 3. Isolation: two runs with distinct stores do not share memory
# ---------------------------------------------------------------------------

class TestRunIsolation:
    def test_two_distinct_requests_have_independent_stores(self) -> None:
        store_a = FakeKnowledgeStore(name="a")
        store_b = FakeKnowledgeStore(name="b")
        req_a = _make_request(knowledge_store=store_a, chat_id="chat-A")
        req_b = _make_request(knowledge_store=store_b, chat_id="chat-B")

        # Each request references its own store.
        assert req_a.knowledge_store is store_a
        assert req_b.knowledge_store is store_b
        assert req_a.knowledge_store is not req_b.knowledge_store

    def test_two_requests_without_store_are_independent(self) -> None:
        """Two default requests each get their own None; MemoryKnowledgeStore is
        created fresh inside the runner, so no shared state."""
        req_a = _make_request()
        req_b = _make_request()
        assert req_a.knowledge_store is None
        assert req_b.knowledge_store is None
        # Neither request carries a shared pre-built store.

    @pytest.mark.asyncio
    async def test_shared_store_instance_reaches_both_runs(self) -> None:
        """An operator may intentionally share a single durable store across runs.

        This is a legitimate use case (e.g. a SQLite store shared across
        sessions for persistent cross-run memory). The seam must support it.
        """
        shared_store = FakeKnowledgeStore(name="shared")
        req_a = _make_request(knowledge_store=shared_store, chat_id="chat-A")
        req_b = _make_request(knowledge_store=shared_store, chat_id="chat-B")

        captured: list[Any] = []

        async def fake_hub_open(store: Any, **kwargs: Any) -> Any:
            captured.append(store)
            hub = MagicMock()
            hub.close = AsyncMock()
            hub.register_listener = MagicMock()
            hub.read_wal = AsyncMock(return_value=[])
            hub.adapter_state = MagicMock(return_value=SimpleNamespace(context_vars={}))
            return hub

        with patch("mozaiksai.core.adapters.ag2_network_runner.Hub.open", fake_hub_open):
            runner = AG2NetworkRunner()
            await runner.run(req_a)
            await runner.run(req_b)

        assert len(captured) == 2
        assert captured[0] is shared_store
        assert captured[1] is shared_store


# ---------------------------------------------------------------------------
# 4. AG2 KnowledgeStore protocol satisfied by FakeKnowledgeStore
# ---------------------------------------------------------------------------

class TestFakeKnowledgeStoreProtocol:
    def test_fake_store_satisfies_ag2_protocol(self) -> None:
        """Prove FakeKnowledgeStore satisfies the AG2 KnowledgeStore Protocol
        so tests above use a legitimate duck-typed implementation."""
        from ag2.knowledge import KnowledgeStore

        store = FakeKnowledgeStore()
        assert isinstance(store, KnowledgeStore), (
            "FakeKnowledgeStore must satisfy the AG2 KnowledgeStore @runtime_checkable Protocol."
        )


# ---------------------------------------------------------------------------
# 5. run_workflow_orchestration accepts knowledge_store kwarg
# ---------------------------------------------------------------------------

class TestOrchestrationPatternsSignature:
    def test_run_workflow_orchestration_accepts_knowledge_store(self) -> None:
        """Verifies the public orchestration entry point declares knowledge_store."""
        import inspect

        from mozaiksai.core.workflow.orchestration_patterns import run_workflow_orchestration

        sig = inspect.signature(run_workflow_orchestration)
        assert "knowledge_store" in sig.parameters, (
            "run_workflow_orchestration must accept a knowledge_store parameter."
        )
        param = sig.parameters["knowledge_store"]
        assert param.default is None, (
            "knowledge_store must default to None for backward compatibility."
        )

    def test_run_ag2_network_phase_accepts_knowledge_store(self) -> None:
        """Verifies the internal phase function accepts knowledge_store."""
        import inspect

        from mozaiksai.core.workflow.orchestration_patterns import _run_ag2_network_phase

        sig = inspect.signature(_run_ag2_network_phase)
        assert "knowledge_store" in sig.parameters
        assert sig.parameters["knowledge_store"].default is None
