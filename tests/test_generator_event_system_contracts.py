from __future__ import annotations

from pathlib import Path

import yaml


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def _read_yaml(relative_path: str):
    return yaml.safe_load(_read(relative_path))


def test_designdocs_defines_event_architecture_before_generation() -> None:
    source = _read("factory_app/workflows/DesignDocs/agents.yaml")

    assert "[EVENT ARCHITECTURE]" in source
    assert "[CONTRACT DISCIPLINE]" in source
    assert "module-owned `domain.*` events" in source
    assert "workflow trigger candidates expressed as domain-event reactions" in source
    assert "UI events are not durable business facts" in source
    assert "strict structured outputs" in source
    assert "do not invent ad hoc YAML keys" in source
    assert "event_model:" in source


def test_appgenerator_build_plan_preserves_event_flows() -> None:
    config = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    models = config["models"]

    assert "AppEventFlow" in models
    assert models["AppBuildPlan"]["fields"]["event_flows"]["items"] == "AppEventFlow"

    source = _read("factory_app/workflows/AppGenerator/agents.yaml")
    assert "**event architecture rule**" in source
    assert "`event_flows[].event_type` must be a namespaced `domain.{module_id}.{event_name}`" in source
    assert "Workflow reactions must reference `workflow_capability_ids`; do not put raw workflow names" in source


def test_appgenerator_page_schema_limits_page_events_to_ui_namespace() -> None:
    source = _read("factory_app/workflows/AppGenerator/agents.yaml")

    assert "Page actions that mutate durable app state should submit to module actions/API endpoints" in source
    assert "Use `ui.*` events only for transient browser reactions" in source
    assert "Do not emit `domain.*`, `workflow.*`, `runtime.*`, `platform.*`, or `hosted.*` from page schemas" in source


def test_agentgenerator_structured_outputs_include_workflow_event_boundary() -> None:
    config = _read_yaml("factory_app/workflows/AgentGenerator/structured_outputs.yaml")
    models = config["models"]

    for model_name in [
        "WorkflowInputEvent",
        "WorkflowModuleActionUse",
        "WorkflowEmittedEvent",
        "WorkflowEventBoundary",
        "OrchestratorTrigger",
    ]:
        assert model_name in models

    assert models["WorkflowStrategy"]["fields"]["event_boundary"]["type"] == "WorkflowEventBoundary"
    assert models["OrchestrationConfigOutput"]["fields"]["triggers"]["items"] == "OrchestratorTrigger"
    assert "capability_id" in models["OrchestratorTrigger"]["fields"]


def test_agentgenerator_prompts_enforce_workflow_domain_event_boundary() -> None:
    source = _read("factory_app/workflows/AgentGenerator/agents.yaml")

    assert "9. **Derive `event_boundary`**" in source
    assert "This workflow does not publish domain.* directly; modules publish domain.* after state commits." in source
    assert "Workflows may react to domain events and may call module capabilities" in source
    assert "Do not invent domain events here" in source
    assert "workflows must never publish `domain.*` directly" in source
    assert "Preserve `capability_id` when `WorkflowStrategy.event_boundary.input_events` declares one." in source
    assert "Do not put capability ids into orchestrator.yaml until the runtime trigger contract supports them" not in source

