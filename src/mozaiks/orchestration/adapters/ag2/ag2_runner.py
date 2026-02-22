"""Optional AG2 adapter implementation.

All AG2 imports are lazy and local to runtime methods.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

from ...domain.agent_spec import AgentSpec
from ...domain.dag import TaskNode
from ...domain.events import CanonicalEvent, MessageDelta, ToolCallCandidate
from ...interfaces.agent_factory import AgentFactory, AgentHandle
from ...interfaces.vendor_event_adapter import VendorEventAdapter


class AG2UnavailableError(RuntimeError):
    """Raised when AG2 adapter is used without the optional dependency."""


def _load_ag2_symbols() -> tuple[type[Any], type[Any]]:
    try:
        from autogen import AssistantAgent, UserProxyAgent
    except Exception as exc:  # pragma: no cover - depends on optional extra
        raise AG2UnavailableError(
            "AG2 is not installed. Install with `pip install mozaiks-ai[ag2]`."
        ) from exc

    return AssistantAgent, UserProxyAgent


class _AG2Agent(AgentHandle):
    def __init__(
        self,
        *,
        spec: AgentSpec,
        llm_config: dict[str, Any] | None,
    ) -> None:
        self._spec = spec
        self._llm_config = dict(llm_config or {})

    async def run(
        self,
        task: TaskNode,
        task_input: Mapping[str, object],
    ) -> AsyncIterator[object]:
        AssistantAgent, UserProxyAgent = _load_ag2_symbols()

        prompt = str(task_input.get("prompt", task.prompt or ""))
        assistant = AssistantAgent(
            name=self._spec.name,
            system_message=self._spec.system_prompt,
            llm_config=self._llm_config or None,
        )
        user_proxy = UserProxyAgent(
            name="user_proxy",
            human_input_mode="NEVER",
            code_execution_config=False,
        )

        if hasattr(user_proxy, "run_iter"):
            iterator = user_proxy.run_iter(assistant, message=prompt)
            if hasattr(iterator, "__aiter__"):
                async for event in iterator:
                    yield event
                return

        # Compatibility path for AG2 versions that do not expose async iterators.
        result = await asyncio.to_thread(
            user_proxy.initiate_chat,
            assistant,
            message=prompt,
        )
        content = getattr(result, "summary", None)
        if not content:
            content = str(result)
        yield {"type": "message", "source": self._spec.name, "content": content}


class AG2AgentFactory(AgentFactory):
    """AgentFactory implementation backed by AG2."""

    def __init__(self, *, default_llm_config: dict[str, Any] | None = None) -> None:
        self._default_llm_config = dict(default_llm_config or {})

    async def create(self, spec: AgentSpec | None = None) -> AgentHandle:
        effective_spec = spec or AgentSpec()
        return _AG2Agent(spec=effective_spec, llm_config=self._default_llm_config)


class AG2VendorEventAdapter(VendorEventAdapter):
    """Converts AG2 runtime events into canonical event types."""

    def normalize(
        self,
        vendor_event: object,
        *,
        run_id: str,
        task_id: str | None,
    ) -> list[CanonicalEvent]:
        if isinstance(vendor_event, dict):
            event_type = str(vendor_event.get("type", "message"))
            if event_type in {"tool_call", "tool.called", "tool_call_candidate"}:
                return [
                    ToolCallCandidate(
                        run_id=run_id,
                        task_id=task_id,
                        tool_name=str(vendor_event.get("name", "unknown_tool")),
                        arguments=dict(vendor_event.get("arguments", {})),
                        source=str(vendor_event.get("source", "ag2")),
                    )
                ]
            return [
                MessageDelta(
                    run_id=run_id,
                    task_id=task_id,
                    source=str(vendor_event.get("source", "ag2")),
                    delta=str(vendor_event.get("content", "")),
                )
            ]

        tool_calls = getattr(vendor_event, "tool_calls", None)
        if tool_calls:
            events: list[CanonicalEvent] = []
            for call in tool_calls:
                call_name = getattr(call, "name", "unknown_tool")
                arguments = getattr(call, "arguments", {})
                if not isinstance(arguments, dict):
                    arguments = {"raw": arguments}
                events.append(
                    ToolCallCandidate(
                        run_id=run_id,
                        task_id=task_id,
                        tool_name=str(call_name),
                        arguments=arguments,
                        source="ag2",
                    )
                )
            return events

        content = getattr(vendor_event, "content", None)
        if content is not None:
            source = getattr(vendor_event, "source", "ag2")
            return [
                MessageDelta(
                    run_id=run_id,
                    task_id=task_id,
                    source=str(source),
                    delta=str(content),
                )
            ]

        return [
            MessageDelta(
                run_id=run_id,
                task_id=task_id,
                source="ag2",
                delta=str(vendor_event),
            )
        ]
