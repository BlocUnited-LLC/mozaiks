"""AppGenerator authority stops at tasks with actual application file outputs."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows._shared.workflow_integration import (
    apply_workflow_integration_context,
    extract_workflow_integration_metadata_from_bundle_entries,
)
from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _ALLOWED_TASK_TYPES,
    _CANONICAL_INITIAL_AGENTS,
    app_build_plan,
)
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_file_contracts_body,
)
from mozaiksai.core.adapters.ag2_task_batch_runner import (
    AG2TaskBatchRunner,
    AG2TaskBatchRunnerResult,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.assignment_kinds import (
    AssignmentKind,
    app_build_assignment_kind_values,
)
from mozaiksai.core.workflow.task_batches import (
    _validate_batch_owned_paths,
    execute_task_batches_for_trigger,
    load_task_batches_config,
)

ROOT = Path(__file__).resolve().parents[1]
RETIRED = "agent_backend_integration"


class Context:
    def __init__(self, data: dict | None = None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def fixture() -> dict:
    return json.loads((ROOT / "tests/fixtures/module-interface-retirement.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("owned_paths", [[], ["workflows/DocumentAnalysis/module_interface.yaml"], ["services/config.py"], ["arbitrary/fake.txt"]])
@pytest.mark.parametrize("surface_kind", [None, "external_integration", "workflow"])
@pytest.mark.parametrize("initial_agent", sorted(set(_CANONICAL_INITIAL_AGENTS.values())))
def test_retired_build_task_fails_closed(owned_paths, surface_kind, initial_agent) -> None:
    plan = fixture()["plan"]
    plan["build_tasks"].append({
        "task_id": "retired-integration",
        "task_type": RETIRED,
        "owned_paths": owned_paths,
        "surface_kind": surface_kind,
        "initial_agent": initial_agent,
    })
    context = Context()
    with pytest.raises(ValueError, match="non-materializing after module-interface retirement.*not valid AppGenerator build-task authority"):
        app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert context.get("app_plan_ready") is None
    assert context.get("app_task_batch_items") is None


def test_invalid_type_remediation_and_planner_use_local_vocabulary() -> None:
    plan = fixture()["plan"]
    plan["build_tasks"].append({"task_id": "invalid", "task_type": "totally_invalid_type", "owned_paths": []})
    with pytest.raises(ValueError, match="unsupported task_type 'totally_invalid_type'") as exc:
        app_build_plan(AppBuildPlan=plan, context_variables=Context())
    recommendation = str(exc.value).split("Use only the canonical AppGenerator task types: ", 1)[1].rstrip(".")
    assert set(recommendation.split(", ")) == _ALLOWED_TASK_TYPES
    assert RETIRED not in recommendation
    body = _build_file_contracts_body(SimpleNamespace(name="AppPlanAgent"), {})
    assert ", ".join(sorted(_ALLOWED_TASK_TYPES)) in body
    assert RETIRED not in body


def test_generic_assignment_and_capability_pack_meanings_remain() -> None:
    assert AssignmentKind.AGENT_BACKEND_INTEGRATION.value == RETIRED
    assert RETIRED in app_build_assignment_kind_values()
    assert RETIRED not in _ALLOWED_TASK_TYPES
    schemas = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    assert set(schemas["models"]["AppBuildTask"]["fields"]["task_type"]["values"]) == _ALLOWED_TASK_TYPES
    assert any(RETIRED in model.get("fields", {}).get("pack_type", {}).get("values", []) for model in schemas["models"].values())


async def materialize_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict, dict]:
    data = fixture()
    plan = data["plan"]
    context = Context({"app_id": "retirement-acceptance", "build_task_model": "AppBuildTask"})
    metadata = extract_workflow_integration_metadata_from_bundle_entries([
        {"workflow_name": "DocumentAnalysis", "files": [{"filename": k, "content": v} for k, v in data["workflow_files"].items()]}
    ], bundle_name="DocumentAnalysis")
    apply_workflow_integration_context(context, metadata)
    before = deepcopy(context.data)
    assert context.get("generated_workflow_name") == "DocumentAnalysis"
    assert context.get("generated_workflow_capability_id") == "document-analysis"
    assert context.get("generated_workflow_trigger_events")[0]["event_type"] == "domain.documents.analysis_requested"

    assert all(task["task_type"] != RETIRED for task in plan["build_tasks"])
    app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert context.get("app_plan_ready") is True
    items = context.get("app_task_batch_items")
    assert len(items) == len(plan["build_tasks"]) == 3
    assert all(item["owned_paths"] and set(item["owned_paths"]) <= data["app_files"].keys() for item in items)
    config = load_task_batches_config("AppGenerator", workflows_root=ROOT / "factory_app/workflows")
    assert config is not None
    assert config.batches[0].result.require_owned_paths is True
    _validate_batch_owned_paths(config.batches[0], items)
    seen = []

    async def run(_self, request):
        task = request.context_variables["current_build_task"]
        seen.append(task["task_id"])
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output={
            "code_files": [{"filename": path, "content": data["app_files"][path]} for path in task["owned_paths"]],
        })

    monkeypatch.setattr(AG2TaskBatchRunner, "run", run)
    await execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={item["initial_agent"]: object() for item in items}, context_variables=context.data,
        chat_id="retirement-chat", app_id="retirement-acceptance", user_id="test-user", fresh_agents_per_task=False,
    )
    assert context.get("app_task_batch_status") == "completed"
    assert set(seen) == {item["task_id"] for item in items}
    results = deepcopy(context.get("app_task_batch_results"))
    context.set("code_files", [{"filename": f"workflows/DocumentAnalysis/{path}", "content": content} for path, content in data["workflow_files"].items()])
    assembled = await assemble_app_tasks(context_variables=context)
    assert context.get("assembled_source") == "schema_and_task_batch_outputs"
    files = {item["filename"]: item["content"] for item in assembled["code_files"]}
    assert files == context.get("generated_files")
    assert all(context.get(key) == value for key, value in before.items())
    return files, context.data, results


@pytest.mark.asyncio
async def test_workflow_module_batch_executes_and_assembles_without_interface(monkeypatch) -> None:
    files, context, results = await materialize_fixture(monkeypatch)
    assert all(Path(path).name != "module_interface.yaml" for path in files)
    assert "modules/documents/module.yaml" in files
    assert "modules/documents/backend/handler.py" in files
    assert "/api/modules/documents/summarize_document" in files["workflows/DocumentAnalysis/agents.yaml"]
    assert "backend_request" in files["workflows/DocumentAnalysis/tools.yaml"]
    assert yaml.safe_load(files["modules/documents/module.yaml"])["actions"][0]["id"] == "summarize_document"
    for path, content in fixture()["workflow_files"].items():
        assert files[f"workflows/DocumentAnalysis/{path}"] == content
    assert context["app_build_plan"]["pages"] == fixture()["plan"]["pages"]
    page_output = next(entry["content"] for entry in results["page_bundle"]["code_files"] if entry["filename"] == "ui/pages/documents.yaml")
    assert files["ui/pages/documents.yaml"] == page_output
