"""Agent factory contract."""

from __future__ import annotations

from typing import AsyncIterator, Mapping, Protocol

from ..domain.agent_spec import AgentSpec
from ..domain.dag import TaskNode


class AgentHandle(Protocol):
    """Executable agent session."""

    async def run(
        self,
        task: TaskNode,
        task_input: Mapping[str, object],
    ) -> AsyncIterator[object]:
        """Yield vendor-native events while executing one task."""
        ...


class AgentFactory(Protocol):
    """Creates agent handles from agent specs."""

    async def create(self, spec: AgentSpec | None = None) -> AgentHandle:
        """Build an agent handle for the requested task spec."""
        ...
