"""Optional smoke fixtures use the existing module contract ownership lane."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_file_contracts_body,
)
from factory_app.workflows.AppGenerator.tools.task_integrity import (
    planned_artifact_diagnostics,
    task_allowed_paths,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.task_batches import optional_task_output_paths
from tests.test_app_plan_review import _context, _plan
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = "modules/reports/contracts/fixtures.yaml"


@pytest.mark.parametrize("agent_name", ["AppPlanAgent", "ConfigMiddlewareAgent", "ServiceAgent"])
def test_actual_file_contract_hook_projects_optional_fixture_boundary(agent_name):
    catalog = yaml.safe_load((ROOT / "factory_app/build_context/AppGenerator/file_contracts.yaml").read_text(encoding="utf-8"))
    context = _context()
    context.set("current_build_task", {"task_type": "module_contract", "capability_pack_id": "reports"})
    rendered = _build_file_contracts_body(SimpleNamespace(name=agent_name, context_variables=context), catalog)
    assert "modules/{pack_name}/contracts/fixtures.yaml" in rendered
    assert "module_contract.fixtures_yaml" in rendered
    assert "trusted contained observer independently owns required checks and outcomes" in rendered


def test_public_plan_review_keeps_fixture_path_optional_in_its_existing_module_task():
    _load_models()
    plan, context = _plan(), _context()
    task = next(item for item in plan["build_tasks"] if item["task_type"] == "module_contract")
    task["owned_paths"].append(FIXTURE_PATH)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    reviewed = detach(context.get("app_build_plan"))
    task = next(item for item in reviewed["build_tasks"] if item["task_type"] == "module_contract")
    assert FIXTURE_PATH in task["owned_paths"]
    assert FIXTURE_PATH in optional_task_output_paths(task)
    assert FIXTURE_PATH in task_allowed_paths(task, reviewed["build_tasks"])
    fixture_task = deepcopy(task)
    fixture_task["owned_paths"] = ["modules/reports/module.yaml", FIXTURE_PATH]
    diagnostics = planned_artifact_diagnostics({
        "app_build_plan": {"build_tasks": [fixture_task]},
        "app_task_batch_results": {task["task_id"]: {"module_contract": {"module_id": "reports"}}},
    }, {"app.json": "{}", "modules/reports/module.yaml": "module: {id: reports}"})
    assert diagnostics == []


def test_optional_fixture_does_not_transfer_ownership_between_modules_or_task_types():
    task = {"task_id": "reports_contract", "task_type": "module_contract", "capability_pack_id": "reports", "owned_paths": []}
    assert FIXTURE_PATH in task_allowed_paths(task, [task])
    assert "modules/other/contracts/fixtures.yaml" not in task_allowed_paths(task, [task])
    service = {**task, "task_type": "business_services"}
    assert FIXTURE_PATH not in task_allowed_paths(service, [service])
    other_owner = {**task, "task_id": "explicit_owner", "owned_paths": [FIXTURE_PATH]}
    assert FIXTURE_PATH not in task_allowed_paths(task, [task, other_owner])
