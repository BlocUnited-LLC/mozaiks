from __future__ import annotations

import asyncio
import json
import os

import pytest

from scripts import smoke_refinement_task_batch as smoke


def _live_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_REFINEMENT_TASK_BATCH_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def test_default_spec_is_provider_neutral() -> None:
    rendered = json.dumps(smoke.DEFAULT_SPEC.__dict__, sort_keys=True).lower()

    assert smoke.DEFAULT_SPEC.workflow_name == "RuntimeTaskBatchSmoke"
    assert "creator onboarding" in rendered
    assert "moderation" in rendered
    assert "notifications" in rendered
    assert "app zero" not in rendered
    assert "mozaiks-app" not in rendered
    assert "blocunited" not in rendered


def test_validate_smoke_output_accepts_minimal_valid_payload() -> None:
    payload = {
        "schema_version": smoke.SCHEMA_VERSION,
        "llm_profile_used": "classifier",
        "generated_files_unchanged": True,
        "refinement_engine": {
            "route": {
                "workflow_sequence": "design_revision",
                "sequence_exists": True,
                "sequence": {
                    "step_count": 3,
                    "workflow_ids": ["DesignDocs", "AgentGenerator", "AppGenerator"],
                    "transition_ids": [],
                    "steps": [
                        {"index": 0, "workflows": ["DesignDocs"]},
                        {"index": 1, "workflows": ["AgentGenerator"]},
                        {"index": 2, "workflows": ["AppGenerator"]},
                    ],
                },
            },
            "impact": {
                "affected_bundle_paths": [
                    "modules/projects/module.yaml",
                    "ui/pages/dashboard.yaml",
                ]
            },
        },
        "live_workflow": {
            "success": True,
            "workflow_name": "RuntimeTaskBatchSmoke",
            "structured_output": {
                "task_batch_execution_used": True,
                "work_unit_count": 4,
                "max_parallelism": 4,
                "executed_task_ids": ["a", "b", "c", "d"],
                "executed_kinds": ["module", "page", "integration"],
                "failure_count": 0,
                "result_context_key": "runtime_smoke_tasks_results",
                "all_units_succeeded": True,
            },
            "final_context": {
                "runtime_smoke_tasks_status": "completed",
                "runtime_smoke_tasks_results": {
                    "_meta": {
                        "status": "completed",
                        "task_count": 4,
                        "concurrency": 4,
                        "completed_tasks": ["a", "b", "c", "d"],
                        "failed_tasks": [],
                        "result_context_key": "runtime_smoke_tasks_results",
                    },
                    "a": {
                        "_worker_agent": "ModuleTaskWorkerAgent",
                    },
                    "b": {
                        "_worker_agent": "PageTaskWorkerAgent",
                    },
                    "c": {
                        "_worker_agent": "IntegrationTaskWorkerAgent",
                    },
                    "d": {
                        "_worker_agent": "ModuleTaskWorkerAgent",
                    },
                },
            },
        },
    }

    assert smoke.validate_smoke_output(payload) == []


def test_validate_smoke_output_rejects_structured_output_meta_drift() -> None:
    payload = {
        "schema_version": smoke.SCHEMA_VERSION,
        "llm_profile_used": "classifier",
        "generated_files_unchanged": True,
        "refinement_engine": {
            "route": {
                "workflow_sequence": "app_revision",
                "sequence_exists": True,
                "sequence": {"step_count": 1, "workflow_ids": ["AppGenerator"], "transition_ids": []},
            },
            "impact": {"affected_bundle_paths": []},
        },
        "live_workflow": {
            "success": True,
            "workflow_name": "RuntimeTaskBatchSmoke",
            "structured_output": {
                "task_batch_execution_used": True,
                "work_unit_count": 1,
                "max_parallelism": 4,
                "executed_task_ids": ["hallucinated"],
                "executed_kinds": ["module"],
                "failure_count": 0,
                "result_context_key": "wrong_key",
                "all_units_succeeded": True,
            },
            "final_context": {
                "runtime_smoke_tasks_status": "completed",
                "runtime_smoke_tasks_results": {
                    "_meta": {
                        "status": "completed",
                        "task_count": 2,
                        "concurrency": 4,
                        "completed_tasks": ["profiles", "feed"],
                        "failed_tasks": [],
                        "result_context_key": "runtime_smoke_tasks_results",
                    },
                    "profiles": {
                        "_worker_agent": "ModuleTaskWorkerAgent",
                    },
                    "feed": {
                        "_worker_agent": "ModuleTaskWorkerAgent",
                    },
                },
            },
        },
    }

    violations = smoke.validate_smoke_output(payload)

    assert (
        "Structured output work_unit_count does not match "
        "runtime_smoke_tasks_results._meta.task_count"
    ) in violations
    assert (
        "Structured output executed_task_ids does not match "
        "runtime_smoke_tasks_results._meta.completed_tasks"
    ) in violations
    assert (
        "Structured output result_context_key does not match "
        "runtime_smoke_tasks_results._meta.result_context_key"
    ) in violations
    assert "Live workflow smoke did not execute module, page, and integration task kinds" in violations
    assert "Live workflow smoke did not use all expected task worker agents" in violations


@pytest.mark.skipif(
    not _live_smoke_enabled(),
    reason="Set RUN_LIVE_REFINEMENT_TASK_BATCH_SMOKE=1 to run the combined live smoke test",
)
def test_live_refinement_task_batch_smoke() -> None:
    payload = asyncio.run(smoke.run_smoke())

    assert payload["success"] is True
    assert payload["refinement_engine"]["route"]["workflow_sequence"]
    assert payload["live_workflow"]["success"] is True
    assert payload["live_workflow"]["structured_output"]["task_batch_execution_used"] is True

