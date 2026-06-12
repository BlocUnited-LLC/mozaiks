from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner


class _RunnerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class _FakeReply:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def content(self) -> Any:
        return self._result


class _FakeAgent:
    def __init__(self, system_prompt: str, llm_config: dict[str, Any], result: Any) -> None:
        self.system_prompt = system_prompt
        self.llm_config = llm_config
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:
        self.calls.append({"user_prompt": user_prompt, **kwargs})
        return _FakeReply(self.result)


@pytest.mark.asyncio
async def test_ag2_structured_agent_runner_calls_agent_with_schema_and_defaults() -> None:
    created: list[_FakeAgent] = []

    def factory(system_prompt: str, llm_config: dict[str, Any]) -> _FakeAgent:
        agent = _FakeAgent(system_prompt, llm_config, _RunnerResponse(status="ok"))
        created.append(agent)
        return agent

    runner = AG2StructuredAgentRunner(agent_factory=factory, stream_factory=lambda: "stream")

    result = await runner.run(
        agent_name="ControlPlaneCheckpoint",
        system_prompt="system",
        user_prompt="user",
        llm_config={"model": "gpt-test", "temperature": 0.0},
        response_schema=_RunnerResponse,
    )

    assert result == _RunnerResponse(status="ok")
    assert len(created) == 1
    assert created[0].system_prompt == "system"
    assert created[0].llm_config == {"model": "gpt-test", "temperature": 0.0}
    assert created[0].calls[0]["user_prompt"] == "user"
    assert created[0].calls[0]["stream"] == "stream"
    assert created[0].calls[0]["response_schema"] is _RunnerResponse
    assert created[0].calls[0]["middleware"]
    assert created[0].calls[0]["observers"]


@pytest.mark.asyncio
async def test_ag2_structured_agent_runner_validates_dict_result() -> None:
    runner = AG2StructuredAgentRunner(
        agent_factory=lambda system_prompt, llm_config: _FakeAgent(
            system_prompt,
            llm_config,
            {"status": "ok"},
        )
    )

    result = await runner.run(
        agent_name="ControlPlaneCheckpoint",
        system_prompt="system",
        user_prompt="user",
        llm_config={},
        response_schema=_RunnerResponse,
    )

    assert result == _RunnerResponse(status="ok")
