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
    validate_generated_workflow_bundle,
)


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_valid_workflow(
    workflow_dir: Path,
    workflow_name: str,
    *,
    startup_mode: str = "AgentDriven",
    visual_agents=None,
    include_task_batches: bool = False,
) -> None:
    workflow_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        workflow_dir / "orchestrator.yaml",
        {
            "workflow_name": workflow_name,
            "max_turns": 4,
            "human_in_the_loop": startup_mode != "BackendOnly",
            "workflow_startup_mode": startup_mode,
            "orchestration_pattern": "ag2_network",
            "initial_message": "Run the smoke workflow.",
            "initial_agent": "PlannerAgent",
            "triggers": [{"type": "chat", "description": "Smoke trigger"}],
        },
    )
    _write_yaml(
        workflow_dir / "agents.yaml",
        {
            "agents": [
                {"name": "PlannerAgent", "system_message": "Plan the workflow."},
                {"name": "WorkerAgent", "system_message": "Execute workflow tasks."},
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
            "definitions": {
                "tenant_id": {"type": "string", "source": {"type": "state", "default": "tenant-1"}},
                "app_id": {"type": "string", "source": {"type": "state", "default": "app-1"}},
                "user_id": {"type": "string", "source": {"type": "state", "default": "user-1"}},
            },
            "agents": {"PlannerAgent": {"variables": ["tenant_id", "app_id", "user_id"]}},
        },
    )
    _write_yaml(
        workflow_dir / "structured_outputs.yaml",
        {
            "models": {},
            "registry": {"PlannerAgent": None, "WorkerAgent": None},
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
                        "execution_agents": ["WorkerAgent"],
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
