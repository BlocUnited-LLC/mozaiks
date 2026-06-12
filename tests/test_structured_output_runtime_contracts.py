from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")
_structured_mod = import_module_directly("mozaiksai.core.workflow.outputs.structured")


def test_agentgenerator_pack_metadata_output_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    models, registry = _structured_mod.load_workflow_structured_outputs("AgentGenerator")

    assert registry["PatternAgent"].__name__ == "PatternSelectionOutput"
    assert registry["PackMetadataAgent"].__name__ == "PackMetadataOutput"

    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["PackMetadataAgent"]
    )
    assert supported is True, f"PackMetadataOutput fails strict mode at: {offending_path}"
    assert offending_path is None

    entrypoint = models["PackGraphEntrypoint"](
        id="create_app",
        path="/create",
        label="Create App",
        transition=None,
        workflow=None,
        sequence=None,
        requiresAuth=False,
        order=1,
        meta=None,
    )
    transition_ui = models["PackGraphTransitionUI"](
        component="WorkflowChooser",
        mode="screen",
        shell_mode=None,
        props=None,
    )
    option = models["PackGraphTransitionOption"](
        id="new_app",
        route_to="ValueEngine",
        sequence=None,
        context_variables=None,
    )

    assert entrypoint.meta is None
    assert transition_ui.shell_mode is None
    assert transition_ui.props is None
    assert option.context_variables is None


def test_appgenerator_structured_outputs_load_task_batch_build_task_contract() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()

    models, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")

    assert registry["AppPlanAgent"].__name__ == "AppBuildPlanOutput"

    task = models["AppBuildTask"](
        task_id="task_service_foundation",
        task_type="service_foundation",
        capability_pack_id=None,
        surface_id="app_shell",
        surface_kind="module",
        execution_target="AppGenerator",
        initial_agent="ConfigMiddlewareAgent",
        description="Backend foundation task",
        initial_message="Execute only the backend foundation task.",
        owned_paths=["services/config.py"],
        depends_on=[],
        acceptance_criteria=["Environment-driven config only"],
        context_variables=[
            {"key": "task_run_mode", "value": "true", "value_type": "boolean"},
            {
                "key": "current_build_task_id",
                "value": "task_service_foundation",
                "value_type": "string",
            },
        ],
        integration_needs=[],
        domain_context=None,
    )

    assert "AppChildWorkflowSpec" not in models
    assert task.initial_agent == "ConfigMiddlewareAgent"
    assert task.context_variables[1].key == "current_build_task_id"


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


def test_appgenerator_config_middleware_output_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")
    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["ConfigMiddlewareAgent"]
    )

    assert supported is True
    assert offending_path is None


def test_appgenerator_provider_schema_has_no_ref_sibling_keywords() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")
    provider_model = _structured_mod.get_provider_response_model(registry["AppSchemaAgent"])
    raw_schema = BaseModel.model_json_schema.__func__(provider_model)

    def _has_ref_sibling_keywords(node) -> bool:
        if isinstance(node, dict):
            if "$ref" in node and len(node) > 1:
                return True
            return any(_has_ref_sibling_keywords(value) for value in node.values())
        if isinstance(node, list):
            return any(_has_ref_sibling_keywords(value) for value in node)
        return False

    assert _has_ref_sibling_keywords(raw_schema) is False
    assert "$ref" not in str(provider_model.model_json_schema())


def test_appgenerator_app_plan_output_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")
    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["AppPlanAgent"]
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
    expected_model = _structured_mod.get_provider_response_model(registry["AppSchemaAgent"])
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
    expected_model = _structured_mod.get_provider_response_model(registry["PatternAgent"])
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


def test_designdocs_agent_output_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("DesignDocs")
    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["DesignDocsAgent"]
    )

    assert supported is True, f"DesignDocsAgent output fails strict mode at: {offending_path}"
    assert offending_path is None


def test_runtime_task_batch_models_mark_declared_fields_as_required() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()

    models, registry = _structured_mod.load_workflow_structured_outputs("RuntimeTaskBatchSmoke")

    planner_output = _structured_mod.get_provider_response_model(registry["TaskPlannerAgent"])
    planner_schema = planner_output.model_json_schema()
    plan_schema = planner_schema["properties"]["DecompositionPlan"]
    work_unit_schema = plan_schema["properties"]["tasks"]["items"]

    assert planner_output.model_fields["agent_message"].is_required() is True
    assert planner_output.model_fields["DecompositionPlan"].is_required() is True
    assert _structured_mod.get_provider_response_model(models["DecompositionPlan"]).model_fields["user_intent"].is_required() is True
    assert _structured_mod.get_provider_response_model(models["DecomposedTask"]).model_fields["task_id"].is_required() is True
    assert _structured_mod.get_provider_response_model(models["DecomposedTask"]).model_fields["owned_paths"].is_required() is True
    assert "default" not in plan_schema["properties"]["user_intent"]
    assert "default" not in work_unit_schema["properties"]["task_id"]


def test_runtime_task_batch_planner_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("RuntimeTaskBatchSmoke")

    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["TaskPlannerAgent"]
    )
    assert supported is True
    assert offending_path is None


def test_valueengine_gap_analysis_output_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("ValueEngine")

    assert registry["GapAnalysisAgent"].__name__ == "ConceptBlueprint"
    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["GapAnalysisAgent"]
    )
    assert supported is True, f"ConceptBlueprint fails strict mode at: {offending_path}"
    assert offending_path is None


def test_themecapture_assembler_output_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("ThemeCapture")

    assert registry["ThemeConfigAssemblerAgent"].__name__ == "CapturedThemeConfig"
    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["ThemeConfigAssemblerAgent"]
    )
    assert supported is True, f"CapturedThemeConfig fails strict mode at: {offending_path}"
    assert offending_path is None


def test_existingappdiscovery_assembler_output_supports_provider_strict_response_format() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(workflows_root))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()

    _, registry = _structured_mod.load_workflow_structured_outputs("ExistingAppDiscovery")

    assert registry["DiscoveryArtifactAssemblerAgent"].__name__ == "ExistingAppAugmentationArtifact"
    supported, offending_path = _structured_mod.supports_provider_response_format(
        registry["DiscoveryArtifactAssemblerAgent"]
    )
    assert supported is True, f"ExistingAppAugmentationArtifact fails strict mode at: {offending_path}"
    assert offending_path is None

