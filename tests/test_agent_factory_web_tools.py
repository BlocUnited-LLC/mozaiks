from __future__ import annotations

from typing import Any

import pytest


class _FakeManager:
    def get_config(self, workflow_name: str) -> dict[str, Any]:
        return {
            "agents": [
                {
                    "name": "ResearchAgent",
                    "structured_outputs_required": False,
                    "prompt_sections": [
                        {"heading": "[ROLE]", "content": "Research the app."}
                    ],
                    "web_search": True,
                    "web_fetch": True,
                }
            ]
        }

    def get_auto_tool_agents(self, workflow_name: str) -> set[str]:
        return set()

    def resolve_workflow_path(self, workflow_name: str) -> None:
        return None


def _tool_names(agent: Any) -> list[str]:
    return [str(getattr(tool, "name", "")) for tool in getattr(agent, "tools", ())]


@pytest.mark.asyncio
async def test_create_agents_skips_native_web_tools_for_openai_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import factory
    from mozaiksai.core.workflow.agents import tools as agent_tools
    from mozaiksai.core.workflow.outputs import structured

    async def _fake_llm_config(*args: Any, **kwargs: Any) -> tuple[None, dict[str, Any]]:
        return None, {
            "config_list": [
                {"model": "gpt-4o-mini", "api_type": "openai", "api_key": "test-key"}
            ]
        }

    monkeypatch.setattr(factory, "workflow_manager", _FakeManager())
    monkeypatch.setattr(factory, "load_a2a_agent_specs", lambda _config: {})
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _workflow: {})
    monkeypatch.setattr(llm_config, "get_llm_config", _fake_llm_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", _fake_llm_config)
    monkeypatch.setattr(agent_tools, "load_agent_tool_functions", lambda _workflow: {})

    agents = await factory.create_agents("ValueEngine", context_variables={})

    assert _tool_names(agents["ResearchAgent"]) == []


@pytest.mark.asyncio
async def test_create_agents_attaches_native_web_tools_for_openai_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import factory
    from mozaiksai.core.workflow.agents import tools as agent_tools
    from mozaiksai.core.workflow.outputs import structured

    async def _fake_llm_config(*args: Any, **kwargs: Any) -> tuple[None, dict[str, Any]]:
        return None, {
            "config_list": [
                {"model": "gpt-4o-mini", "api_type": "openai", "api_key": "test-key"}
            ],
            "use_responses_api": True,
        }

    monkeypatch.setattr(factory, "workflow_manager", _FakeManager())
    monkeypatch.setattr(factory, "load_a2a_agent_specs", lambda _config: {})
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _workflow: {})
    monkeypatch.setattr(llm_config, "get_llm_config", _fake_llm_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", _fake_llm_config)
    monkeypatch.setattr(agent_tools, "load_agent_tool_functions", lambda _workflow: {})

    agents = await factory.create_agents("ValueEngine", context_variables={})

    assert _tool_names(agents["ResearchAgent"]) == ["web_search", "web_fetch"]
