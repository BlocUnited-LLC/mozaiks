"""Generated replay declarations use the runtime agent contract without a second schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AgentGenerator.tools.generate_and_download import _write_bundle_to_disk
from factory_app.workflows.AgentGenerator.tools.outcome_materialization import (
    materialize_workflow_outcomes,
)
from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    validate_workflow_bundle_structure,
)
from mozaiksai.core.workflow.declarative.contracts import (
    AgentsConfig,
    AgentSpec,
    parse_agents_config,
)
from mozaiksai.core.workflow.outputs.structured import build_models_from_config
from tests.test_workflow_tool_outcomes import _entry

WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


@pytest.fixture(scope="module")
def bundle_model():
    config = yaml.safe_load((WORKFLOWS / "AgentGenerator" / "structured_outputs.yaml").read_text(encoding="utf-8"))
    return build_models_from_config(config["models"], exact_model_ids=frozenset(config["models"]))[
        config["registry"]["WorkflowBundleBuilderAgent"]
    ]


def _candidate(bundle_model, policy):
    entry = _entry()
    entry.update(agent_message="Document workflow generated.", pattern_id=1, pattern_name="Sequential")
    for file in entry["files"]:
        file["installRequirements"] = []
        if file["filename"] == "agents.yaml":
            agents = yaml.safe_load(file["content"])
            if policy is not None:
                agents["agents"][1]["pending_turn_replay"] = policy
            file["content"] = yaml.safe_dump(agents, sort_keys=False)
    for route in entry["outcome_plans"][0]["routes"]:
        route["termination_reason"] = None
    return bundle_model.model_validate(entry).model_dump(mode="json")


@pytest.mark.parametrize("policy", [None, "allow", "block"])
def test_generated_pending_replay_survives_structured_output_materialization_and_loading(
    bundle_model, tmp_path, policy,
):
    candidate = _candidate(bundle_model, policy)
    emitted = materialize_workflow_outcomes(candidate)
    assert emitted != candidate  # Exercise real outcome materialization, not its empty-plan path.
    assert emitted == materialize_workflow_outcomes(emitted)
    report = validate_workflow_bundle_structure(bundle_entries=[emitted])
    assert report["valid"], report["errors"]

    agents_file = next(file for file in candidate["files"] if file["filename"] == "agents.yaml")
    workflow_dir, _ = _write_bundle_to_disk(emitted["workflow_name"], emitted["files"], tmp_path)
    written = (workflow_dir / "agents.yaml").read_text(encoding="utf-8")
    assert written == agents_file["content"]
    parsed = parse_agents_config(yaml.safe_load(written))
    agents = parsed["agents"]
    assert agents["RepairAgent"]["pending_turn_replay"] == (policy or "allow")
    assert agents["CheckAgent"]["pending_turn_replay"] == "allow"
    assert agents["DoneAgent"]["pending_turn_replay"] == "allow"


@pytest.mark.parametrize("policy", ["retry", "BLOCK", True])
def test_generated_unknown_pending_replay_policy_fails_canonical_quality_gate(bundle_model, policy):
    candidate = _candidate(bundle_model, policy)
    report = validate_workflow_bundle_structure(bundle_entries=[candidate])
    assert not report["valid"]
    assert any("pending_turn_replay" in error and "'allow' or 'block'" in error for error in report["errors"])


def test_appgenerator_only_artifact_workers_block_pending_replay_in_canonical_roundtrip():
    schema = AgentSpec.model_json_schema()["properties"]["pending_turn_replay"]
    assert schema["enum"] == ["allow", "block"]
    assert schema["default"] == "allow"

    declared = yaml.safe_load((WORKFLOWS / "AppGenerator" / "agents.yaml").read_text(encoding="utf-8"))
    validated = AgentsConfig.model_validate(declared)
    roundtrip = AgentsConfig.model_validate(yaml.safe_load(yaml.safe_dump(validated.model_dump())))
    assert roundtrip == validated
    blocked = {
        "AppSchemaAgent", "DatabaseAgent", "ConfigMiddlewareAgent", "RefinementHarnessAgent",
        "ModelAgent", "ServiceAgent", "FrontendStubAgent", "ControllerAgent",
    }
    assert {agent.name for agent in roundtrip.agents if agent.pending_turn_replay == "block"} == blocked
    unrelated = [agent for agent in roundtrip.agents if agent.name not in blocked]
    assert unrelated
    assert all(agent.pending_turn_replay == "allow" for agent in unrelated)
