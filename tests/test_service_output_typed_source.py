"""ServiceAgent emits source once; canonical consumers build the file representation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from tests.test_continuous_deterministic_materialization import _load_models

WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app/workflows"
SERVICE_PATH = "modules/reports/backend/service.py"
SOURCE = "def summarize_titles(records):\n    return [row['title'] for row in records]\n"


@pytest.fixture(scope="module")
def service_model():
    return _load_models()["ServiceOutput"]


def _payload(path: str = SERVICE_PATH) -> dict:
    return {
        "python_files": [{
            "path": path, "kind": "service", "purpose": "Summarize report titles.",
            "contract_refs": [], "content": SOURCE,
        }],
        "deleted_files": [],
        "agent_message": "Implemented the report helper.",
    }


def test_typed_source_is_sufficient_for_model_validation_and_file_extraction(service_model):
    output = service_model.model_validate(_payload()).model_dump(mode="json")
    assert "code_files" not in output
    assert extract_code_file_map_from_payload(output) == {SERVICE_PATH: SOURCE}


@pytest.mark.parametrize("mirror", [[], [{"filename": SERVICE_PATH, "content": "conflicting source"}]])
def test_service_model_rejects_the_obsolete_authored_mirror(service_model, mirror):
    with pytest.raises(ValidationError) as rejected:
        service_model.model_validate({**_payload(), "code_files": mirror})
    assert any(error["loc"] == ("code_files",) and error["type"] == "extra_forbidden"
               for error in rejected.value.errors())


def test_typed_only_output_reaches_persisted_files_and_keeps_deletion_semantics(service_model):
    obsolete = "modules/reports/backend/old_helper.py"
    payload = {**_payload(), "deleted_files": [obsolete]}
    output = service_model.model_validate(payload).model_dump(mode="json")
    context = ContextVariablesBridge({
        "code_files": [{"filename": obsolete, "content": "old source"}],
        "current_build_task": {"owned_paths": [SERVICE_PATH, obsolete]},
    })

    result = save_generated_code(StructuredOutputOverlay(context, output))

    assert result == {"saved_files": [SERVICE_PATH], "deleted_files": [obsolete]}
    assert context.snapshot()["code_files"] == [{"filename": SERVICE_PATH, "content": SOURCE}]
    assert context.snapshot()["deleted_files"] == [obsolete]
    assert "code_files" not in output


async def _admit_typed_service(monkeypatch, output):
    task = {
        "task_id": "report_services", "task_type": "business_services", "initial_agent": "ServiceAgent",
        "initial_message": "Implement the report helper.", "owned_paths": [SERVICE_PATH], "depends_on": [],
    }
    context = {"app_build_plan": {"build_tasks": [task], "capability_packs": [], "pages": []}}

    async def run(_runner, _request):
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=deepcopy(output))

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS)
    assert config is not None
    return await task_batches._run_one_task(
        workflow_name="AppGenerator", batch=config.batches[0], task=task, all_task_items=[task],
        base_context=context, completed_task_outputs={}, current_batch_outputs={}, agents={"ServiceAgent": object()},
        chat_id="typed-service", app_id="typed-service", user_id="owner", semaphore=asyncio.Semaphore(1),
        fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
    )


@pytest.mark.asyncio
async def test_task_admission_materializes_internal_code_files_from_typed_source(service_model, monkeypatch):
    output = service_model.model_validate(_payload()).model_dump(mode="json")

    admitted = await _admit_typed_service(monkeypatch, output)

    assert admitted["code_files"] == [{"filename": SERVICE_PATH, "content": SOURCE}]
    assert admitted["python_files"] == output["python_files"]
    assert "code_files" not in output


@pytest.mark.asyncio
async def test_typed_only_task_still_rejects_files_outside_its_ownership(service_model, monkeypatch):
    foreign = "modules/other/backend/service.py"
    output = service_model.model_validate(_payload(foreign)).model_dump(mode="json")

    with pytest.raises(task_batches._TaskRejected) as rejected:
        await _admit_typed_service(monkeypatch, output)

    assert rejected.value.evidence["failure_kind"] == "output_rejected"
    assert foreign in rejected.value.evidence["error"]
