"""Run a workflow's declared text-producing tool as one native AG2 turn."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Never, Self
from uuid import uuid4

from ag2 import Agent, ToolResult
from ag2.config import LLMClient, ModelConfig
from ag2.events import BaseEvent, ModelResponse, ToolCallEvent, ToolCallsEvent


@dataclass(frozen=True)
class _ReplyToolConfig(ModelConfig):
    """AG2 opens a fresh client per turn. Its only decision is a fixed local tool call."""

    name: str

    @property
    def provider(self) -> Never:
        raise NotImplementedError("Local reply tools have no model provider.")

    @property
    def model(self) -> str:
        return "reply_tool"

    def create_files_client(self) -> Never:
        raise NotImplementedError("Local reply tools do not use a files provider.")

    def copy(self) -> Self:
        return self

    def create(self) -> LLMClient:
        requested = False

        async def request_tool(messages: Sequence[BaseEvent], context: Any, **kwargs: Any) -> ModelResponse:
            nonlocal requested
            if requested:
                # An exception or malformed tool result must not re-enter the tool or a provider.
                raise RuntimeError("The workflow reply tool could not produce a reply.")
            requested = True
            call = ToolCallEvent(id=str(uuid4()), name=self.name, arguments="{}")
            return ModelResponse(tool_calls=ToolCallsEvent([call]))

        return request_tool


def create_reply_tool_agent(name: str, tool_name: str, functions: list[Callable]) -> Agent:
    if len(functions) != 1 or functions[0].__name__ != tool_name:
        raise ValueError(f"{name}: reply_tool must resolve to its single bound function {tool_name!r}")
    function = functions[0]
    if inspect.signature(function).parameters:
        raise ValueError(f"{name}: reply_tool cannot take arguments other than runtime context_variables")

    async def reply() -> ToolResult:
        if inspect.iscoroutinefunction(function):
            text = await function()
        else:
            text = await asyncio.to_thread(function)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{name}: reply_tool must return nonempty text")
        return ToolResult(text, final=True)

    reply.__name__ = tool_name
    return Agent(name, config=_ReplyToolConfig(tool_name), tools=[reply])
