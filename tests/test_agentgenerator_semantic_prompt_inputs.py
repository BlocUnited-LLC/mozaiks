"""Workflow consumers reason over the semantic inputs their runtime views expose."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.workflow.context.context_utils import apply_context_exposures
from mozaiksai.core.workflow.context.schema import load_context_variables_config

_WORKFLOW = Path(__file__).resolve().parents[1] / "factory_app/workflows/AgentGenerator"


@pytest.fixture(scope="module")
def workflow_inputs():
    agents = yaml.safe_load((_WORKFLOW / "agents.yaml").read_text(encoding="utf-8"))
    context = load_context_variables_config(
        yaml.safe_load((_WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))
    )
    prompts = {
        agent["name"]: "\n".join(
            section["content"] for section in agent["prompt_sections"]
            if section["id"] in {"objective", "context", "instructions"}
        )
        for agent in agents["agents"]
    }
    return prompts, context


@pytest.mark.parametrize("agent,required_inputs", [
    ("ProjectOverviewAgent", {
        "workflows_spec": "planned_scope_sentinel",
        "concept_overview": "product_intent_sentinel",
        "pack_name": "pack_title_sentinel",
        "pack_partition_reason": "partition_reason_sentinel",
    }),
    ("PackMetadataAgent", {
        "workflows_spec": "planned_scope_sentinel",
        "workflow_bundle_results": "generated_file_sentinel",
        "pack_name": "pack_title_sentinel",
        "pack_partition_reason": "partition_reason_sentinel",
    }),
    ("DownloadAgent", {
        "workflow_bundle_results": "generated_file_sentinel",
        "workflow_bundle_validation_errors": "validation_failure_sentinel",
    }),
])
def test_consumers_receive_the_semantic_inputs_their_prompts_request(
    workflow_inputs, agent, required_inputs,
):
    prompts, context_plan = workflow_inputs
    context = {
        "concept_overview": "product_intent_sentinel",
        "pack_name": "pack_title_sentinel",
        "pack_partition_reason": "partition_reason_sentinel",
        "is_multi_workflow": False,
        "workflows_spec": [{
            "name": "RecordsReview", "description": "planned_scope_sentinel",
            "role": "primary", "pattern_name": "Pipeline", "depends_on": [],
        }],
        "workflow_bundle_results": {"records_review": {
            "workflow_name": "RecordsReview",
            "files": [{"filename": "agents.yaml", "content": "generated_file_sentinel"}],
        }},
        "workflow_bundle_validation_status": "failed",
        "workflow_bundle_validation_errors": ["validation_failure_sentinel"],
        "workflow_bundle_repair_status": "blocked",
    }
    hidden_inputs = {
        key: f"unexposed_{key}_sentinel"
        for key in ("PatternSelection", "PackMetadata", "PatternAgent", "PackMetadataAgent")
    }
    prompt = prompts[agent]
    rendered = apply_context_exposures(
        prompt, [], {**context, **hidden_inputs}, context_plan.agents[agent].variables,
    )

    for variable, sentinel in required_inputs.items():
        assert re.search(rf"\b{variable}\b", prompt), (agent, variable)
        assert f"{variable.upper()}:" in rendered
        assert sentinel in rendered
    assert not re.search(r"\b(?:PatternSelection|PackMetadata)\b", prompt)
    for sentinel in hidden_inputs.values():
        assert sentinel not in rendered


def test_empty_workflow_partition_does_not_fall_back_to_an_unexposed_selection(workflow_inputs):
    prompts, context_plan = workflow_inputs
    rendered = apply_context_exposures(
        prompts["ProjectOverviewAgent"], [], {
            "workflows_spec": [], "is_multi_workflow": False,
            "pack_name": "Customer Records",
            "pack_partition_reason": "Deterministic modules and pages cover the approved scope.",
            "PatternSelection": {"workflows": [{"name": "StaleWorkflowFromPriorDraft"}]},
        }, context_plan.agents["ProjectOverviewAgent"].variables,
    )
    assert "WORKFLOWS_SPEC: []" in rendered
    assert "Deterministic modules and pages cover the approved scope." in rendered
    assert "StaleWorkflowFromPriorDraft" not in rendered
