from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.core.workflow.context.context_utils import (
    apply_context_exposures,
)


def test_apply_context_exposures_uses_agent_variable_fallback() -> None:
    system_message = apply_context_exposures(
        "Generate the app UI.",
        [],
        {
            "app_build_plan": {
                "app_name": "Support Operations",
                "pages": [{"name": "Tickets", "route": "/tickets"}],
            }
        },
        ["app_build_plan"],
    )

    assert "Generate the app UI." in system_message
    assert "APP_BUILD_PLAN" in system_message
    assert "SUPPORT OPERATIONS" in system_message.upper()
    assert "/tickets" in system_message


@pytest.mark.asyncio
async def test_create_agents_exposes_declared_context_variables_without_explicit_exposures(monkeypatch) -> None:
    from mozaiksai.core.workflow.agents import factory
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import tools as agent_tools
    from mozaiksai.core.workflow.outputs import structured

    class _FakeManager:
        def get_config(self, workflow_name: str):
            return {
                "agents": [
                    {
                        "name": "AppSchemaAgent",
                        "prompt_sections": [
                            {"heading": "[ROLE]", "content": "Generate the app UI."}
                        ],
                    }
                ]
            }

        def get_auto_tool_agents(self, workflow_name: str):
            return set()

        def resolve_workflow_path(self, workflow_name: str):
            return None

    class _Context:
        _mozaiks_context_agents = {
            "AppSchemaAgent": SimpleNamespace(variables=["app_build_plan"])
        }

        def __init__(self) -> None:
            self.data = {
                "app_build_plan": {
                    "app_name": "Support Operations",
                    "pages": [{"name": "Tickets", "route": "/tickets"}],
                }
            }

        def get(self, key: str, default=None):
            return self.data.get(key, default)

    async def _fake_llm_config(*args, **kwargs):
        return None, {"config_list": [{"model": "gpt-4o-mini", "api_key": "test-key"}]}

    monkeypatch.setattr(factory, "workflow_manager", _FakeManager())
    monkeypatch.setattr(factory, "load_a2a_agent_specs", lambda _config: {})
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _workflow: {})
    monkeypatch.setattr(llm_config, "get_llm_config", _fake_llm_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", _fake_llm_config)
    monkeypatch.setattr(agent_tools, "load_agent_tool_functions", lambda _workflow: {})

    agents = await factory.create_agents("AppGenerator", context_variables=_Context())

    assert "AppSchemaAgent" in agents
    system_message = getattr(agents["AppSchemaAgent"], "_mozaiks_base_system_message")
    assert "APP_BUILD_PLAN" in system_message
    assert "SUPPORT OPERATIONS" in system_message.upper()


def test_persisted_session_context_overrides_declared_defaults() -> None:
    from mozaiksai.core.workflow.orchestration_patterns import _merge_persisted_extra_context

    class _Context:
        def __init__(self) -> None:
            self.data = {
                "interview_complete": False,
                "app_plan_ready": False,
                "capability_packs": [],
                "app_build_plan": None,
            }

        def get(self, key: str, default=None):
            return self.data.get(key, default)

        def set(self, key: str, value) -> None:
            self.data[key] = value

    context = _Context()

    _merge_persisted_extra_context(
        context,
        {
            "interview_complete": True,
            "app_plan_ready": True,
            "capability_packs": [{"pack_id": "wallet"}],
            "app_build_plan": {"app_name": "Support Operations"},
            "parent_chat_id": "chat-parent",
        },
    )

    assert context.get("interview_complete") is True
    assert context.get("app_plan_ready") is True
    assert context.get("capability_packs") == [{"pack_id": "wallet"}]
    assert context.get("app_build_plan") == {"app_name": "Support Operations"}
    assert context.get("automated_workflow_run") is True


@pytest.mark.asyncio
async def test_initial_agent_override_suppresses_orchestrator_seed() -> None:
    from mozaiksai.core.workflow.orchestration_patterns import _resume_or_initialize_chat

    class _Persistence:
        async def load_run_history(self, *, chat_id: str, app_id: str):
            return []

        async def create_chat_session(self, **kwargs):
            return None

    logger = SimpleNamespace(info=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)

    _, initial_messages = await _resume_or_initialize_chat(
        persistence_manager=_Persistence(),
        config={
            "initial_message": "InterviewAgent: Greet the user and ask questions.",
            "workflow_startup_mode": "AgentDriven",
        },
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="AppGenerator",
        user_id="user-1",
        initial_message="Generate from seeded context.",
        initial_agent_name="AppSchemaAgent",
        wf_logger=logger,
        suppress_config_seed=True,
    )

    assert [message["content"] for message in initial_messages] == ["Generate from seeded context."]


@pytest.mark.asyncio
async def test_default_workflow_start_keeps_orchestrator_seed() -> None:
    from mozaiksai.core.workflow.orchestration_patterns import _resume_or_initialize_chat

    class _Persistence:
        async def load_run_history(self, *, chat_id: str, app_id: str):
            return []

        async def create_chat_session(self, **kwargs):
            return None

    logger = SimpleNamespace(info=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)

    _, initial_messages = await _resume_or_initialize_chat(
        persistence_manager=_Persistence(),
        config={
            "initial_message": "InterviewAgent: Greet the user and ask questions.",
            "workflow_startup_mode": "AgentDriven",
        },
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="AppGenerator",
        user_id="user-1",
        initial_message="User prompt.",
        initial_agent_name="InterviewAgent",
        wf_logger=logger,
    )

    assert [message["content"] for message in initial_messages] == [
        "InterviewAgent: Greet the user and ask questions.",
        "User prompt.",
    ]

