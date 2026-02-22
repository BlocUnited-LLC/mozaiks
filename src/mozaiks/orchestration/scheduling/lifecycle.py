"""Workflow runner composition and default engine factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from ..adapters.ag2 import AG2AgentFactory, AG2VendorEventAdapter
from ..adapters.mock import MockAgentFactory, MockVendorEventAdapter
from ..domain.events import CanonicalEvent
from ..domain.requests import ResumeRequest, RunRequest
from ..interfaces.ai_runner import AIWorkflowRunner
from ..interfaces.stream_adapter import StreamAdapter
from ..tools.registry import RegistryToolBinder, ToolRegistry
from .orchestrator import DefaultOrchestrator
from .stores import InMemoryCheckpointStore, InMemoryEventSink, JsonStreamAdapter


class WorkflowEngineRunner(AIWorkflowRunner):
    """Default AIWorkflowRunner implementation."""

    def __init__(
        self,
        *,
        orchestrator: DefaultOrchestrator,
        checkpoint_store: InMemoryCheckpointStore,
        stream_adapter: StreamAdapter | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._checkpoint_store = checkpoint_store
        self._stream_adapter = stream_adapter or JsonStreamAdapter()

    async def run(self, request: RunRequest) -> AsyncIterator[CanonicalEvent]:
        async for event in self._orchestrator.start(
            plan=request.dag,
            run_id=request.run_id,
            initial_input=request.initial_input,
            metadata=request.metadata,
            runtime_context=request.runtime_context,
        ):
            yield event

    async def resume(self, request: ResumeRequest) -> AsyncIterator[CanonicalEvent]:
        checkpoint = await self._checkpoint_store.load(request.run_id)
        if checkpoint is None:
            raise KeyError(f"Checkpoint not found for run '{request.run_id}'.")

        async for event in self._orchestrator.resume(
            plan=request.dag,
            checkpoint=checkpoint,
            runtime_context=request.runtime_context,
        ):
            yield event

    async def cancel(self, run_id: str) -> None:
        await self._orchestrator.cancel(run_id)

    def to_frame(self, event: CanonicalEvent) -> dict[str, object]:
        return self._stream_adapter.to_frame(event)


@dataclass(slots=True)
class EngineRuntime:
    """Default composition root used in tests and local execution."""

    runner: WorkflowEngineRunner
    orchestrator: DefaultOrchestrator
    tool_registry: ToolRegistry
    event_sink: InMemoryEventSink
    checkpoint_store: InMemoryCheckpointStore


def create_default_engine(
    *,
    adapter: str = "mock",
    default_llm_config: dict[str, object] | None = None,
) -> EngineRuntime:
    """Create a fully in-memory engine with a selected execution adapter."""
    tool_registry = ToolRegistry()
    event_sink = InMemoryEventSink()
    checkpoint_store = InMemoryCheckpointStore()
    tool_binder = RegistryToolBinder(tool_registry)

    normalized_adapter = adapter.strip().lower() or "mock"
    if normalized_adapter == "ag2":
        agent_factory = AG2AgentFactory(default_llm_config=default_llm_config)
        vendor_event_adapter = AG2VendorEventAdapter()
    else:
        agent_factory = MockAgentFactory()
        vendor_event_adapter = MockVendorEventAdapter()

    orchestrator = DefaultOrchestrator(
        agent_factory=agent_factory,
        tool_binder=tool_binder,
        vendor_event_adapter=vendor_event_adapter,
        event_sink=event_sink,
        checkpoint_store=checkpoint_store,
        tool_catalog={spec.name: spec for spec in tool_registry.list_specs()},
    )
    runner = WorkflowEngineRunner(
        orchestrator=orchestrator,
        checkpoint_store=checkpoint_store,
    )
    return EngineRuntime(
        runner=runner,
        orchestrator=orchestrator,
        tool_registry=tool_registry,
        event_sink=event_sink,
        checkpoint_store=checkpoint_store,
    )
