"""Mock vendor adapter used for local runs and tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from ...domain.agent_spec import AgentSpec
from ...domain.dag import TaskNode
from ...domain.events import CanonicalEvent, MessageDelta, ToolCallCandidate
from ...interfaces.agent_factory import AgentFactory, AgentHandle
from ...interfaces.vendor_event_adapter import VendorEventAdapter


class _MockAgent(AgentHandle):
    def __init__(self, spec: AgentSpec | None) -> None:
        self._spec = spec or AgentSpec()

    async def run(
        self,
        task: TaskNode,
        task_input: Mapping[str, object],
    ) -> AsyncIterator[object]:
        prompt = str(task_input.get("prompt", ""))
        speaker = self._spec.name

        candidate_tool_calls = task.metadata.get("candidate_tool_calls")
        if isinstance(candidate_tool_calls, list):
            for item in candidate_tool_calls:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "unknown_tool"))
                arguments = dict(item.get("arguments", {}))
                yield {
                    "type": "tool_call_candidate",
                    "name": name,
                    "arguments": arguments,
                    "source": speaker,
                }
        elif "candidate_tool_name" in task.metadata:
            yield {
                "type": "tool_call_candidate",
                "name": str(task.metadata["candidate_tool_name"]),
                "arguments": dict(task.metadata.get("candidate_tool_arguments", {})),
                "source": speaker,
            }

        if "tool_name" in task.metadata:
            yield {
                "type": "tool_call",
                "name": str(task.metadata["tool_name"]),
                "arguments": dict(task.metadata.get("tool_arguments", {})),
                "source": speaker,
            }

        if prompt:
            yield {
                "type": "message",
                "source": speaker,
                "content": f"{speaker}: {prompt}",
            }
        else:
            yield {
                "type": "message",
                "source": speaker,
                "content": f"{speaker}: task {task.task_id} executed",
            }


class MockAgentFactory(AgentFactory):
    """Factory creating mock agents."""

    async def create(self, spec: AgentSpec | None = None) -> AgentHandle:
        return _MockAgent(spec)


class MockVendorEventAdapter(VendorEventAdapter):
    """Normalizes mock events into canonical events."""

    def normalize(
        self,
        vendor_event: object,
        *,
        run_id: str,
        task_id: str | None,
    ) -> list[CanonicalEvent]:
        if isinstance(vendor_event, dict):
            event_type = str(vendor_event.get("type", "message"))
            if event_type in {"tool_call_candidate", "tool.call_candidate"}:
                return [
                    ToolCallCandidate(
                        run_id=run_id,
                        task_id=task_id,
                        tool_name=str(vendor_event.get("name", "unknown_tool")),
                        arguments=dict(vendor_event.get("arguments", {})),
                        source=str(vendor_event.get("source", "mock")),
                    )
                ]
            if event_type in {"tool_call", "tool.called"}:
                return [
                    ToolCallCandidate(
                        run_id=run_id,
                        task_id=task_id,
                        tool_name=str(vendor_event.get("name", "unknown_tool")),
                        arguments=dict(vendor_event.get("arguments", {})),
                        source=str(vendor_event.get("source", "mock")),
                    )
                ]

            return [
                MessageDelta(
                    run_id=run_id,
                    task_id=task_id,
                    source=str(vendor_event.get("source", "mock")),
                    delta=str(vendor_event.get("content", "")),
                )
            ]

        return [
            MessageDelta(
                run_id=run_id,
                task_id=task_id,
                source="mock",
                delta=str(vendor_event),
            )
        ]
