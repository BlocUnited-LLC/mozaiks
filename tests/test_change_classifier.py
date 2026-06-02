from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import (
    ChangeClassifierResult,
    ControlPlaneCapabilityConfig,
    ControlPlaneCheckpointManifest,
    ControlPlaneConfig,
    ControlPlaneHarnessManifest,
    ControlPlaneManifest,
    ControlPlaneProfileInfo,
    ControlPlanePromptDefinition,
    ControlPlanePromptsManifest,
    ControlPlaneToolContext,
    ControlPlaneToolsManifest,
    ControlPlaneToolDefinition,
    ControlPlaneToolResult,
    LLMChangeClassifier,
    LoadedControlPlanePack,
)


# ---------------------------------------------------------------------------
# Fake AG2 agent infrastructure
# ---------------------------------------------------------------------------

class _FakeReply:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.body = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)

    async def content(self, *, retries: int = 0) -> Any:
        return self._result


class _FakeAgent:
    """Records ask() calls and returns a preset ChangeClassifierResult."""

    def __init__(self, system_prompt: str, llm_config: dict[str, Any]) -> None:
        self.system_prompt = system_prompt
        self.llm_config = llm_config
        self.calls: list[dict] = []

    async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:
        self.calls.append({"user_prompt": user_prompt, **kwargs})
        return _FakeReply(
            ChangeClassifierResult(
                change_class="feature",
                rationale="Adds a new capability.",
                confidence=0.8,
                signals=["new_capability"],
            )
        )


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_tool(self, call, *, context=None):  # noqa: ANN001, ANN003
        assert isinstance(context, ControlPlaneToolContext)
        self.calls.append({"call": call, "context": context})
        return ControlPlaneToolResult(success=True, output={"tool_id": call.tool_id, "app_id": context.app_id})


def _enabled_control_plane() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        classifier=ControlPlaneCapabilityConfig(
            enabled=True,
            llm_config={
                "model": "gpt-5-nano",
                "temperature": 0.0,
            },
        ),
    )


def _pack() -> LoadedControlPlanePack:
    return LoadedControlPlanePack(
        path=Path("factory_app/control_plane"),
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.control_plane",
            profile=ControlPlaneProfileInfo(
                id="factory_app",
                display_name="Factory App Harness",
                description="App-zero declarative control-plane pack for the first-party Mozaiks build experience.",
            ),
            harness=ControlPlaneHarnessManifest(
                implementation="mozaiksai.control_plane.implementations.orchestration_control:OrchestrationControlHarness",
                supported_trigger_sources=["refinement"],
            ),
            checkpoints=[
                ControlPlaneCheckpointManifest(
                    id="request_intake",
                    event="request_submitted",
                    entrypoint="mozaiksai.control_plane.implementations.change_classifier:LLMChangeClassifier",
                    prompt_id="change_classifier_system",
                    tool_ids=["get_revision_context", "get_artifact_summary"],
                ),
                ControlPlaneCheckpointManifest(
                    id="route",
                    event="route_requested",
                    entrypoint="mozaiksai.control_plane.implementations.refinement_router:RefinementTriggerRouteResolver",
                ),
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.control_plane.prompts",
            prompts=[
                ControlPlanePromptDefinition(
                    id="change_classifier_system",
                    content="system prompt from pack",
                )
            ],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.control_plane.tools",
            tools=[
                ControlPlaneToolDefinition(
                    id="get_revision_context",
                    kind="context_tool",
                    description="Load revision context",
                    entrypoint="example.tools:get_revision_context",
                    available_to=["request_submitted"],
                ),
                ControlPlaneToolDefinition(
                    id="get_artifact_summary",
                    kind="context_tool",
                    description="Load artifact state",
                    entrypoint="example.tools:get_artifact_summary",
                    available_to=["request_submitted"],
                ),
            ],
        ),
    )


@pytest.mark.asyncio
async def test_change_classifier_uses_control_plane_llm_config() -> None:
    agent = _FakeAgent("", {})
    tool_executor = _FakeToolExecutor()

    classifier = LLMChangeClassifier(
        agent_factory=lambda system_prompt, llm_config: _FakeAgent(system_prompt, llm_config),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=tool_executor,
    )
    # Capture the agent that the factory creates
    created: list[_FakeAgent] = []

    def capturing_factory(system_prompt: str, llm_config: dict) -> _FakeAgent:
        a = _FakeAgent(system_prompt, llm_config)
        created.append(a)
        return a

    classifier._agent_factory = capturing_factory  # type: ignore[assignment]

    result = await classifier.classify(
        artifact_kind="app_bundle",
        artifact_key="app_bundle",
        raw_user_request="Add exports for reporting",
        app_id="app_1",
    )

    assert isinstance(result, ChangeClassifierResult)
    assert result.change_class == "feature"
    assert result.rationale == "Adds a new capability."
    assert result.confidence == 0.8
    assert result.signals == ["new_capability"]

    assert len(created) == 1
    assert created[0].system_prompt == "system prompt from pack"
    assert created[0].llm_config == {"model": "gpt-5-nano", "temperature": 0.0}

    assert len(created[0].calls) == 1
    assert "control_plane_context_json:" in created[0].calls[0]["user_prompt"]
    assert '"get_revision_context"' in created[0].calls[0]["user_prompt"]

    assert len(tool_executor.calls) == 2
    assert tool_executor.calls[0]["context"].artifact_kind == "app_bundle"
    assert tool_executor.calls[0]["context"].artifact_key == "app_bundle"


@pytest.mark.asyncio
async def test_change_classifier_requires_enabled_control_plane() -> None:
    classifier = LLMChangeClassifier(
        agent_factory=lambda sp, lc: _FakeAgent(sp, lc),
        config_loader=lambda: ControlPlaneConfig(enabled=False),
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
    )

    with pytest.raises(RuntimeError, match="Control-plane harness is disabled"):
        await classifier.classify(
            artifact_kind="app_bundle",
            raw_user_request="Add exports for reporting",
            app_id="app_1",
        )
