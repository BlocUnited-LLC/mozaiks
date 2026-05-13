from __future__ import annotations

import asyncio
from pathlib import Path

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")
_structured_mod = import_module_directly("mozaiksai.core.workflow.outputs.structured")


def test_agentgenerator_structured_outputs_load_optional_dict_contracts() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()

    models, registry = _structured_mod.load_workflow_structured_outputs("AgentGenerator")

    assert registry["PatternAgent"].__name__ == "PatternSelectionOutput"
    assert registry["PackMetadataAgent"].__name__ == "PackMetadataOutput"

    entrypoint = models["PackGraphEntrypoint"](
        id="create_app",
        path="/create",
        label="Create App",
        transition=None,
        workflow=None,
        sequence=None,
        requiresAuth=False,
        order=1,
    )
    transition_ui = models["PackGraphTransitionUI"](
        component="WorkflowChooser",
        mode="screen",
    )
    option = models["PackGraphTransitionOption"](
        id="new_app",
        route_to="ValueEngine",
    )

    assert entrypoint.meta is None
    assert transition_ui.shell_mode is None
    assert transition_ui.props is None
    assert option.context_variables is None


def test_appgenerator_structured_outputs_load_child_workflow_spec_contract() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()

    models, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")

    assert registry["AppPlanAgent"].__name__ == "AppBuildPlanOutput"

    child = models["AppChildWorkflowSpec"](
        name="AppGenerator",
        description="Backend foundation task",
        initial_agent="ConfigMiddlewareAgent",
        initial_message="Execute only the backend foundation task.",
        context_variables={"task_run_mode": True, "current_build_task_id": "task_backend_foundation"},
    )

    assert child.name == "AppGenerator"
    assert child.context_variables["current_build_task_id"] == "task_backend_foundation"


def test_appgenerator_app_schema_output_schema_uses_strict_section_config_union() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")
    schema = registry["AppSchemaAgent"].model_json_schema()
    section_schema = schema["properties"]["pages"]["items"]["properties"]["sections"]["items"]
    config_schema = section_schema["properties"]["config"]

    assert section_schema["required"] == [
        "id",
        "primitive",
        "title",
        "config",
        "event_triggers",
        "roles",
    ]
    assert "anyOf" in config_schema
    assert "additionalProperties" not in config_schema


def test_appgenerator_app_schema_output_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")
    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["AppSchemaAgent"]
    )

    assert supported is True
    assert offending_path is None


def test_get_llm_for_workflow_keeps_response_format_for_app_schema_agent() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")
    expected_model = registry["AppSchemaAgent"]
    seen: dict[str, object] = {}

    async def _fake_get_llm_config(*, response_format=None, extra_config=None, cache=True):
        seen["response_format"] = response_format
        return None, {"config_list": [], "tools": []}

    original = _structured_mod.get_llm_config
    _structured_mod.get_llm_config = _fake_get_llm_config
    try:
        _, cfg = asyncio.run(
            _structured_mod.get_llm_for_workflow(
                "AppGenerator",
                agent_name="AppSchemaAgent",
            )
        )
    finally:
        _structured_mod.get_llm_config = original

    assert seen["response_format"] is expected_model
    assert cfg == {"config_list": [], "tools": []}


def test_get_llm_for_workflow_keeps_response_format_for_strict_safe_models() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("AgentGenerator")
    expected_model = registry["PatternAgent"]
    seen: dict[str, object] = {}

    async def _fake_get_llm_config(*, response_format=None, extra_config=None, cache=True):
        seen["response_format"] = response_format
        return None, {"config_list": [], "tools": [], "response_format": response_format}

    original = _structured_mod.get_llm_config
    _structured_mod.get_llm_config = _fake_get_llm_config
    try:
        _, cfg = asyncio.run(
            _structured_mod.get_llm_for_workflow(
                "AgentGenerator",
                agent_name="PatternAgent",
            )
        )
    finally:
        _structured_mod.get_llm_config = original

    assert seen["response_format"] is expected_model
    assert cfg["response_format"] is expected_model
