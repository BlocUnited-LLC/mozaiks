from __future__ import annotations

from pathlib import Path

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")
_structured_mod = import_module_directly("mozaiksai.core.workflow.outputs.structured")


def test_agentgenerator_structured_outputs_load_optional_dict_contracts() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "app" / "workflows"

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
    assert transition_ui.props is None
    assert option.context_variables is None
