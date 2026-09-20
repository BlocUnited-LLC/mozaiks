from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import yaml

from scripts.smoke_agentgenerator_live_pack import (
    REQUIRED_WORKFLOW_FILES,
    _max_overlapping_task_runs,
    build_seeded_pack_context,
    promote_and_load_generated_workflows,
    run_live_agentgenerator_pack_smoke,
    validate_agentgenerator_semantic_drift,
    validate_generated_workflow_bundle,
)

WORKSPACE = Path(__file__).resolve().parents[1]


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_valid_workflow(
    workflow_dir: Path,
    workflow_name: str,
    *,
    startup_mode: str = "AgentDriven",
    visual_agents=None,
    triggers: list[dict] | None = None,
    include_task_batches: bool = False,
) -> None:
    workflow_dir.mkdir(parents=True, exist_ok=True)
    context_definitions = {
        "tenant_id": {"type": "string", "source": {"type": "state", "default": "tenant-1"}},
        "app_id": {"type": "string", "source": {"type": "state", "default": "app-1"}},
        "user_id": {"type": "string", "source": {"type": "state", "default": "user-1"}},
    }
    context_agent_variables = ["tenant_id", "app_id", "user_id"]
    if include_task_batches:
        context_definitions.update(
            {
                "triage_tasks_results": {"type": "array", "source": {"type": "state", "default": []}},
                "triage_tasks_status": {"type": "object", "source": {"type": "state", "default": {}}},
            }
        )
        context_agent_variables.extend(["triage_tasks_results", "triage_tasks_status"])
    _write_yaml(
        workflow_dir / "orchestrator.yaml",
        {
            "schema_version": "mozaiks.orchestrator.v1",
            "workflow_name": workflow_name,
            "max_turns": 4,
            "human_in_the_loop": startup_mode != "BackendOnly",
            "workflow_startup_mode": startup_mode,
            "orchestration_pattern": "ag2_network",
            "initial_message": "Run the smoke workflow.",
            "initial_agent": "PlannerAgent",
            "triggers": triggers or [{"type": "chat", "description": "Smoke trigger"}],
        },
    )
    _write_yaml(
        workflow_dir / "agents.yaml",
        {
            "agents": [
                {
                    "name": "PlannerAgent",
                    "structured_outputs_required": False,
                    "system_message": "Plan the workflow.",
                },
                {
                    "name": "WorkerAgent",
                    "structured_outputs_required": False,
                    "system_message": "Execute workflow tasks.",
                },
                {
                    "name": "AnalysisAgent",
                    "structured_outputs_required": False,
                    "system_message": "Execute specialist analysis tasks.",
                },
            ]
        },
    )
    _write_yaml(
        workflow_dir / "transition_graph.yaml",
        {
            "transition_rules": [
                {
                    "source_agent": "PlannerAgent",
                    "target_agent": "terminate",
                    "transition_type": "after_turn",
                }
            ]
        },
    )
    _write_yaml(
        workflow_dir / "context_variables.yaml",
        {
            "definitions": context_definitions,
            "agents": {"PlannerAgent": {"variables": context_agent_variables}},
        },
    )
    _write_yaml(
        workflow_dir / "structured_outputs.yaml",
        {
            "schema_version": "mozaiks.structured_outputs.v1",
            "models": {},
            "registry": {"PlannerAgent": None, "WorkerAgent": None, "AnalysisAgent": None},
        },
    )
    _write_yaml(workflow_dir / "tools.yaml", {"tools": [], "lifecycle_tools": []})
    _write_yaml(workflow_dir / "middleware.yaml", {"prompt_middleware": []})
    _write_yaml(workflow_dir / "ui_config.yaml", {"visual_agents": visual_agents})
    if include_task_batches:
        _write_yaml(
            workflow_dir / "extended_orchestration" / "task_batches.yaml",
            {
                "version": 1,
                "conveyors": [
                    {
                        "id": "triage_tasks",
                        "decomposition_agent": "PlannerAgent",
                        "execution_agents": ["WorkerAgent", "AnalysisAgent"],
                        "concurrency": 2,
                        "require_owned_paths": False,
                    }
                ],
            },
        )


def test_build_seeded_pack_context_has_parallel_agentgenerator_work_items() -> None:
    context = build_seeded_pack_context(pack_name="Smoke Pack")
    specs = context["workflows_spec"]

    assert context["is_multi_workflow"] is True
    assert context["pack_name"] == "Smoke Pack"
    assert len(specs) == 2
    assert {item["initial_agent"] for item in specs} == {"WorkflowBundleBuilderAgent"}
    assert all(item["depends_on"] == [] for item in specs)
    assert any(item["context_variables"]["require_task_batches"] for item in specs)
    routing_spec = next(item for item in specs if item["name"] == "SupportTicketRoutingWorkflow")
    assert "ClassifierAgent, RoutingAgent" in routing_spec["initial_message"]
    assert "ClassifierAgent -> RoutingAgent -> terminate" in routing_spec["initial_message"]
    conveyor_spec = next(item for item in specs if item["name"] == "TicketBatchTriageWorkflow")
    assert conveyor_spec["context_variables"]["expected_task_batch_id"] == "ticket_batch_triage_tasks"
    assert conveyor_spec["context_variables"]["expected_workflow_capability_id"] == "ticket-batch-triage-workflow"
    assert "capability_id: ticket-batch-triage-workflow" in conveyor_spec["initial_message"]
    assert "ticket_batch_triage_tasks_results" in conveyor_spec["initial_message"]
    assert "ticket_batch_triage_tasks_status" in conveyor_spec["initial_message"]
    assert "distinct declared execution agents" in conveyor_spec["initial_message"]
    assert "do not repeat the same worker name" in conveyor_spec["initial_message"]


def test_workflow_bundle_builder_prompt_forbids_punctuated_yaml_scalars() -> None:
    agents_yaml = yaml.safe_load(
        (WORKSPACE / "factory_app" / "workflows" / "AgentGenerator" / "agents.yaml").read_text(encoding="utf-8")
    )
    agents = agents_yaml["agents"] if isinstance(agents_yaml, dict) else agents_yaml
    builder = next(agent for agent in agents if agent["name"] == "WorkflowBundleBuilderAgent")
    prompt = "\n".join(section["content"] for section in builder["prompt_sections"])

    assert "YAML scalar values must be exact values" in prompt
    assert "Write `prompt_sections` in block YAML style" in prompt
    assert "Never emit inline prompt sections" in prompt
    assert "ticket_triage_tasks_results" in prompt
    assert "ticket_triage_tasks_status" in prompt
    assert "Every agent referenced by transition_graph.yaml must be declared" in prompt
    assert "HandlingLaneAgent" in prompt
    assert "Never omit or null `capability_id`" in prompt
    assert "Trigger for ... event" in prompt
    assert "`execution_agents[]` must contain at least two distinct declared agents" in prompt
    assert "Do not repeat the same worker name" in prompt
    assert "BackendOnly/headless workflows still include ui_config.yaml" in prompt
    assert "never omit ui_config.yaml" in prompt
    assert "transition_type: after_turn." in prompt
    assert "structured_outputs_required: true." in prompt
    assert "require_owned_paths: false." in prompt


def test_max_overlapping_task_runs_counts_concurrent_intervals() -> None:
    records = [
        {"started_perf": 1.0, "ended_perf": 5.0},
        {"started_perf": 2.0, "ended_perf": 4.0},
        {"started_perf": 6.0, "ended_perf": 7.0},
    ]

    assert _max_overlapping_task_runs(records) == 2


def test_validate_generated_workflow_bundle_accepts_current_contract(tmp_path: Path) -> None:
    workflow_name = "TicketBatchTriageWorkflow"
    bundle_root = tmp_path / "bundle"
    _write_valid_workflow(
        bundle_root / workflow_name,
        workflow_name,
        startup_mode="BackendOnly",
        visual_agents=None,
        include_task_batches=True,
    )
    spec = {
        "name": workflow_name,
        "context_variables": {
            "expected_workflow_startup_mode": "BackendOnly",
            "require_task_batches": True,
        },
    }

    validation = validate_generated_workflow_bundle(bundle_root=bundle_root, expected_workflows=[spec])

    assert validation["valid"] is True
    assert validation["errors"] == []
    assert set(REQUIRED_WORKFLOW_FILES).issubset(
        {
            str(path.relative_to(bundle_root / workflow_name)).replace("\\", "/")
            for path in (bundle_root / workflow_name).rglob("*")
            if path.is_file()
        }
    )


def test_validate_agentgenerator_semantic_drift_accepts_domain_event_trigger_contract(tmp_path: Path) -> None:
    workflow_name = "TicketBatchTriageWorkflow"
    bundle_root = tmp_path / "bundle"
    _write_valid_workflow(
        bundle_root / workflow_name,
        workflow_name,
        startup_mode="BackendOnly",
        visual_agents=None,
        triggers=[
            {
                "type": "event",
                "event": "domain.support_ticket.batch_requested",
                "capability_id": "ticket-batch-triage-workflow",
                "description": "Requests parallel support ticket triage for queued tickets.",
            }
        ],
        include_task_batches=True,
    )
    spec = {
        "name": workflow_name,
        "description": "Decompose large ticket queues into parallel triage work units.",
        "context_variables": {
            "expected_workflow_startup_mode": "BackendOnly",
            "expected_human_in_the_loop": False,
            "expected_event_trigger": "domain.support_ticket.batch_requested",
            "expected_workflow_capability_id": "ticket-batch-triage-workflow",
            "require_task_batches": True,
        },
    }

    drift = validate_agentgenerator_semantic_drift(bundle_root=bundle_root, expected_workflows=[spec])

    assert drift["valid"] is True
    assert drift["errors"] == []


def test_validate_agentgenerator_semantic_drift_rejects_trigger_capability_and_description_drift(
    tmp_path: Path,
) -> None:
    workflow_name = "TicketBatchTriageWorkflow"
    bundle_root = tmp_path / "bundle"
    _write_valid_workflow(
        bundle_root / workflow_name,
        workflow_name,
        startup_mode="BackendOnly",
        visual_agents=None,
        triggers=[
            {
                "type": "event",
                "event": "domain.support_ticket.batch_requested",
                "description": "Trigger for batch requested event.",
            }
        ],
        include_task_batches=True,
    )
    spec = {
        "name": workflow_name,
        "description": "Decompose large ticket queues into parallel triage work units.",
        "context_variables": {
            "expected_workflow_startup_mode": "BackendOnly",
            "expected_human_in_the_loop": False,
            "expected_event_trigger": "domain.support_ticket.batch_requested",
            "expected_workflow_capability_id": "ticket-batch-triage-workflow",
            "require_task_batches": True,
        },
    }

    drift = validate_agentgenerator_semantic_drift(bundle_root=bundle_root, expected_workflows=[spec])

    assert drift["valid"] is False
    check_ids = {
        item["check_id"]
        for item in drift["workflows"][0]["semantic_drifts"]
        if item["severity"] == "error"
    }
    assert "event_trigger_capability_id_semantic_drift" in check_ids
    assert "event_trigger_description_semantic_drift" in check_ids
    assert "WorkflowBundleBuilderAgent" in drift["errors"][0]


def test_validate_agentgenerator_semantic_drift_rejects_single_worker_conveyor(tmp_path: Path) -> None:
    workflow_name = "TicketBatchTriageWorkflow"
    bundle_root = tmp_path / "bundle"
    _write_valid_workflow(
        bundle_root / workflow_name,
        workflow_name,
        startup_mode="BackendOnly",
        visual_agents=None,
        triggers=[
            {
                "type": "event",
                "event": "domain.support_ticket.batch_requested",
                "capability_id": "ticket-batch-triage-workflow",
                "description": "Requests parallel support ticket triage for queued tickets.",
            }
        ],
        include_task_batches=True,
    )
    task_batches_path = bundle_root / workflow_name / "extended_orchestration" / "task_batches.yaml"
    task_batches = yaml.safe_load(task_batches_path.read_text(encoding="utf-8"))
    task_batches["conveyors"][0]["execution_agents"] = ["WorkerAgent"]
    _write_yaml(task_batches_path, task_batches)

    drift = validate_agentgenerator_semantic_drift(
        bundle_root=bundle_root,
        expected_workflows=[
            {
                "name": workflow_name,
                "description": "Decompose large ticket queues into parallel triage work units.",
                "context_variables": {
                    "expected_human_in_the_loop": False,
                    "expected_event_trigger": "domain.support_ticket.batch_requested",
                    "expected_workflow_capability_id": "ticket-batch-triage-workflow",
                    "require_task_batches": True,
                },
            }
        ],
    )

    assert drift["valid"] is False
    assert any(
        item["check_id"] == "task_conveyor_parallel_execution_agent_drift"
        for item in drift["workflows"][0]["semantic_drifts"]
    )


def test_validate_generated_workflow_bundle_rejects_conveyor_context_key_drift(tmp_path: Path) -> None:
    workflow_name = "TicketBatchTriageWorkflow"
    bundle_root = tmp_path / "bundle"
    _write_valid_workflow(
        bundle_root / workflow_name,
        workflow_name,
        startup_mode="BackendOnly",
        visual_agents=None,
        include_task_batches=True,
    )
    context_path = bundle_root / workflow_name / "context_variables.yaml"
    context_variables = yaml.safe_load(context_path.read_text(encoding="utf-8"))
    context_variables["definitions"].pop("triage_tasks_results")
    context_variables["definitions"].pop("triage_tasks_status")
    _write_yaml(context_path, context_variables)

    validation = validate_generated_workflow_bundle(
        bundle_root=bundle_root,
        expected_workflows=[
            {
                "name": workflow_name,
                "context_variables": {
                    "expected_workflow_startup_mode": "BackendOnly",
                    "require_task_batches": True,
                },
            }
        ],
    )

    assert validation["valid"] is False
    joined = "\n".join(validation["errors"])
    assert "task_batches 'triage_tasks' writes undeclared context variable 'triage_tasks_results'" in joined
    assert "task_batches 'triage_tasks' writes undeclared context variable 'triage_tasks_status'" in joined


def test_validate_generated_workflow_bundle_rejects_undeclared_transition_agent(tmp_path: Path) -> None:
    workflow_name = "SupportTicketRoutingWorkflow"
    bundle_root = tmp_path / "bundle"
    _write_valid_workflow(bundle_root / workflow_name, workflow_name)
    transition_graph_path = bundle_root / workflow_name / "transition_graph.yaml"
    transition_graph = yaml.safe_load(transition_graph_path.read_text(encoding="utf-8"))
    transition_graph["transition_rules"] = [
        {
            "source_agent": "PlannerAgent",
            "target_agent": "GhostAgent",
            "transition_type": "after_turn",
        }
    ]
    _write_yaml(transition_graph_path, transition_graph)

    validation = validate_generated_workflow_bundle(
        bundle_root=bundle_root,
        expected_workflows=[
            {
                "name": workflow_name,
                "context_variables": {
                    "expected_workflow_startup_mode": "AgentDriven",
                    "require_task_batches": False,
                },
            }
        ],
    )

    assert validation["valid"] is False
    joined = "\n".join(validation["errors"])
    assert "transition graph does not compile through AG2 adapter" in joined
    assert "GhostAgent" in joined


def test_validate_generated_workflow_bundle_rejects_removed_groupchat_fields(tmp_path: Path) -> None:
    workflow_name = "SupportTicketReviewWorkflow"
    bundle_root = tmp_path / "bundle"
    _write_valid_workflow(bundle_root / workflow_name, workflow_name)
    orchestrator = yaml.safe_load((bundle_root / workflow_name / "orchestrator.yaml").read_text(encoding="utf-8"))
    orchestrator.pop("workflow_startup_mode")
    orchestrator["startup_mode"] = "AgentDriven"
    orchestrator["visual_agents"] = []
    orchestrator["triggers"] = [{"event": "domain.ticket.created", "source": "workflow"}]
    _write_yaml(bundle_root / workflow_name / "orchestrator.yaml", orchestrator)
    transition_graph = yaml.safe_load(
        (bundle_root / workflow_name / "transition_graph.yaml").read_text(encoding="utf-8")
    )
    transition_graph["transition_rules"].insert(
        0,
        {
            "source_agent": "PlannerAgent",
            "target_agent": "WorkerAgent",
            "transition_type": "condition",
            "condition": "ready",
        },
    )
    _write_yaml(bundle_root / workflow_name / "transition_graph.yaml", transition_graph)

    validation = validate_generated_workflow_bundle(
        bundle_root=bundle_root,
        expected_workflows=[
            {
                "name": workflow_name,
                "context_variables": {
                    "expected_workflow_startup_mode": "AgentDriven",
                    "require_task_batches": False,
                },
            }
        ],
    )

    assert validation["valid"] is False
    joined = "\n".join(validation["errors"])
    assert "startup_mode" in joined
    assert "visual_agents" in joined
    assert "unsupported keys" in joined
    assert "removed condition field" in joined


def test_promote_and_load_generated_workflows_uses_runtime_loader(tmp_path: Path) -> None:
    workflow_name = "SupportTicketReviewWorkflow"
    bundle_root = tmp_path / "bundle"
    active_root = tmp_path / "active"
    _write_valid_workflow(bundle_root / workflow_name, workflow_name, visual_agents=["PlannerAgent"])

    result = promote_and_load_generated_workflows(
        bundle_root=bundle_root,
        expected_workflows=[{"name": workflow_name}],
        active_root=active_root,
    )

    assert result["valid"] is True
    assert result["errors"] == []
    assert result["loaded"][workflow_name]["status"] == "loaded"


def _live_pack_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_AGENTGENERATOR_PACK_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(
    not _live_pack_smoke_enabled(),
    reason="Set RUN_LIVE_AGENTGENERATOR_PACK_SMOKE=1 to run the live AgentGenerator pack smoke",
)
def test_live_agentgenerator_pack_smoke() -> None:
    result = asyncio.run(run_live_agentgenerator_pack_smoke(timeout_seconds=600.0))

    assert result["success"] is True, result.get("validation_errors")
    assert result["task_batch_meta"]["task_count"] == 2
    assert result["task_run_trace"]["max_overlap"] >= 2
    assert result["semantic_drift"]["valid"] is True
    assert result["semantic_drift"]["errors"] == []
