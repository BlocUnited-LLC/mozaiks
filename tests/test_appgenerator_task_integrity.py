from copy import deepcopy
from pathlib import Path
from types import MappingProxyType

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.task_integrity import (
    artifact_snapshot_digest,
    planned_artifact_diagnostics,
    task_allowed_paths,
)
from mozaiksai.core.workflow.task_batches import task_evidence_digest, task_inventory_digest


def _task(task_id, agent, paths, dependencies=(), task_type="business_services"):
    return {
        "task_id": task_id, "task_type": task_type, "initial_agent": agent,
        "owned_paths": paths, "depends_on": list(dependencies),
    }


def _context():
    tasks = [
        _task("models", "ModelAgent", ["modules/records/backend/schemas.py"]),
        _task("service", "ServiceAgent", ["modules/records/backend/handler.py"], ["models"]),
        _task("pages", "AppSchemaAgent", ["app.json", "ui/pages/records.yaml"], ["service"], "page_bundle"),
    ]
    files = {
        "modules/records/backend/schemas.py": "class Record: pass\n",
        "modules/records/backend/handler.py": "class RecordsModule: pass\n",
        "app.json": '{"appName": "Records"}',
        "ui/pages/records.yaml": "name: records\n",
    }
    return {
        "app_build_plan": {"build_tasks": deepcopy(tasks)},
        "app_task_batch_items": deepcopy(tasks),
        "app_task_batch_results": {
            task["task_id"]: {"code_files": [
                {"filename": path, "content": files[path]} for path in task["owned_paths"]
            ]} for task in tasks
        },
        "generated_files": deepcopy(files),
        "code_files": [{"filename": path, "content": content} for path, content in files.items()],
        "bundle_repair_target": "ServiceAgent",
        "bundle_repair_result": {"active": {
            "task_id": "service", "target_agent": "ServiceAgent", "request_id": "repair-1",
            "allowed_paths": ["modules/records/backend/handler.py"], "status": "selected",
        }},
    }


def test_final_artifacts_require_accepted_task_evidence_even_when_files_exist():
    context = _context()
    results = context["app_task_batch_results"]
    del results["service"]
    del results["pages"]
    results["_failed"] = {
        "service": {"error": "emitted files outside owned_paths: ['modules/records/backend/schemas.py']"},
        "pages": {"error": "dependency 'service' failed", "blocked_by": ["service"]},
    }

    diagnostics = planned_artifact_diagnostics(context, context["generated_files"])

    assert [(item["code"], item["task_id"]) for item in diagnostics] == [
        ("TASK_FAILED", "service"), ("TASK_DEPENDENCY_BLOCKED", "pages"),
    ]
    assert diagnostics[0]["error"].endswith(results["_failed"]["service"]["error"])
    assert diagnostics[1]["blocked_by"] == ["service"]


def test_missing_historical_root_error_stays_missing_and_blocks():
    context = _context()
    del context["app_task_batch_results"]["service"]
    context["app_task_batch_results"]["_meta"] = {"failed_tasks": ["service"]}
    diagnostics = planned_artifact_diagnostics(context, context["generated_files"])
    assert diagnostics[0]["code"] == "TASK_FAILED"
    assert diagnostics[0]["block_reason"] == "original task failure diagnostic unavailable"


def test_interrupted_attempt_blocks_even_with_candidate_artifacts():
    context = _context()
    context["app_task_batch_results"]["_meta"] = {"in_flight": {"service": {"attempt": 2}}}
    diagnostics = planned_artifact_diagnostics(context, context["generated_files"])
    assert diagnostics[0]["code"] == "TASK_EXECUTION_UNCERTAIN"


def test_missing_required_paths_block_while_canonical_optional_paths_remain_optional():
    context = _context()
    for tasks in (context["app_build_plan"]["build_tasks"], context["app_task_batch_items"]):
        tasks[-1]["owned_paths"].append("brand/theme_config.json")
    files = dict(context["generated_files"])
    assert planned_artifact_diagnostics(context, files) == []
    del files["ui/pages/records.yaml"]
    diagnostics = planned_artifact_diagnostics(context, files)
    assert [(item["code"], item["path"]) for item in diagnostics] == [
        ("PLANNED_ARTIFACT_MISSING", "ui/pages/records.yaml"),
    ]


def test_removing_plan_task_does_not_turn_missing_output_into_completion():
    context = _context()
    context["app_build_plan"]["build_tasks"].pop()
    assert planned_artifact_diagnostics(context, context["generated_files"])[0]["code"] == "PLAN_INVENTORY_INVALID"
    context.pop("app_build_plan")
    context.pop("app_task_batch_items")
    assert planned_artifact_diagnostics(context, context["generated_files"])[0]["code"] == "PLAN_INVENTORY_UNAVAILABLE"


def test_generic_validation_without_factory_inventory_is_not_a_task_execution_gate():
    assert planned_artifact_diagnostics({}, {"app.json": "{}"}) == []


@pytest.mark.parametrize("binding", [
    {"workflow_name": "AppGenerator"}, {"app_plan_ready": True},
    {"app_task_batch_status": "completed"},
    {"run_build_binding": {"target_app_id": "records", "build_id": "build-1", "phase": "genesis"}},
])
def test_factory_generation_requires_plan_even_when_all_task_evidence_is_removed(binding):
    context = _context()
    files = context["generated_files"]
    for key in ("app_build_plan", "app_task_batch_items", "app_task_batch_results"):
        context.pop(key)
    context.update(binding)
    diagnostics = planned_artifact_diagnostics(context, files)
    assert diagnostics[0]["code"] == "PLAN_INVENTORY_INVALID"
    assert "requires approved app_build_plan.build_tasks" in diagnostics[0]["error"]


@pytest.mark.parametrize("plan", [None, {}, "invalid", {"build_tasks": None}, {"build_tasks": {}}])
def test_factory_generation_cannot_replace_missing_plan_with_execution_projection(plan):
    context = _context()
    context.update(workflow_name="AppGenerator", app_build_plan=plan)
    assert planned_artifact_diagnostics(context, context["generated_files"])[0]["code"] == "PLAN_INVENTORY_INVALID"


def test_explicit_empty_revision_inventory_does_not_invent_new_task_requirements():
    context = {
        "workflow_name": "AppGenerator", "build_mode": "revision",
        "artifact_version_id": "verified-baseline",
        "app_build_plan": {"build_tasks": [], "pages": [{"name": "records"}]},
        "app_task_batch_items": [],
    }
    assert planned_artifact_diagnostics(context, {"app.json": "{}", "ui/pages/records.yaml": "name: records"}) == []


def test_revision_baseline_cannot_substitute_for_failed_new_task():
    context = _context()
    context.update(build_mode="revision", artifact_version_id="verified-baseline")
    context["generated_files"]["modules/retained/backend/handler.py"] = "UNCHANGED"
    assert planned_artifact_diagnostics(context, context["generated_files"]) == []
    del context["app_task_batch_results"]["service"]
    assert planned_artifact_diagnostics(context, context["generated_files"])[0]["code"] == "TASK_EVIDENCE_UNAVAILABLE"


@pytest.mark.parametrize("operation", ["write", "delete"])
def test_service_agent_foreign_schema_candidate_is_rejected_atomically(operation):
    context = _context()
    before = deepcopy(context)
    payload = {"code_files": [{
        "filename": "modules/records/backend/handler.py", "content": "class Repaired: pass\n",
    }]}
    foreign = "modules/records/backend/schemas.py"
    if operation == "write":
        payload["code_files"].append({"filename": foreign, "content": "class Foreign: pass\n"})
    else:
        payload["deleted_files"] = [foreign]
    context["structured_output"] = payload

    result = save_generated_code(context)

    assert result["status"] == "rejected"
    assert foreign in result["error"]
    assert context["generated_files"] == before["generated_files"]
    assert context["code_files"] == before["code_files"]
    assert context["app_task_batch_results"] == before["app_task_batch_results"]
    assert context["bundle_repair_result"]["active"]["status"] == "rejected"


def test_valid_service_repair_preserves_successes_and_ignores_identical_readback():
    context = _context()
    before = deepcopy(context)
    context["structured_output"] = {"code_files": [
        {"filename": "modules/records/backend/handler.py", "content": "class Repaired: pass\n"},
        {"filename": "modules/records/backend/schemas.py", "content": context["generated_files"]["modules/records/backend/schemas.py"]},
    ]}
    result = save_generated_code(context)
    assert result["saved_files"] == ["modules/records/backend/handler.py"]
    files = {item["filename"]: item["content"] for item in context["code_files"]}
    for path, content in before["generated_files"].items():
        if path != "modules/records/backend/handler.py":
            assert files[path] == content
    assert context["app_task_batch_results"] == before["app_task_batch_results"]
    assert context["bundle_repair_result"]["active"]["status"] == "responded"


def test_repair_request_cannot_widen_owner_or_replay_a_settled_response():
    context = _context()
    context["structured_output"] = {"code_files": [{"filename": "modules/records/backend/handler.py", "content": "new"}]}
    context["bundle_repair_result"]["active"]["allowed_paths"].append("modules/records/backend/schemas.py")
    assert save_generated_code(context)["status"] == "rejected"
    assert save_generated_code(context)["status"] == "rejected"


def test_invalid_typed_contract_repair_settles_without_mutating_accepted_files():
    context = _context()
    task = _task("contract", "ConfigMiddlewareAgent", ["modules/records/module.yaml"], task_type="module_contract")
    context["app_build_plan"]["build_tasks"].append(deepcopy(task))
    context["app_task_batch_items"].append(deepcopy(task))
    context["bundle_repair_target"] = "ConfigMiddlewareAgent"
    context["bundle_repair_result"]["active"].update(
        task_id="contract", target_agent="ConfigMiddlewareAgent", allowed_paths=task["owned_paths"],
    )
    before = deepcopy(context)
    context["structured_output"] = {"module_contract": {
        "module_id": "records", "module_yaml": {"actions": [{"input_schema": {
            "type": "object", "required": [],
            "properties": [{"name": "name", "type": "string", "required": True}],
        }}]},
    }}

    result = save_generated_code(context)

    assert result["status"] == "rejected"
    assert "required" in result["error"]
    assert context["bundle_repair_result"]["active"]["status"] == "rejected"
    assert context["code_files"] == before["code_files"]
    assert context["generated_files"] == before["generated_files"]
    assert context["app_task_batch_results"] == before["app_task_batch_results"]

    context.pop("bundle_repair_result")
    context.pop("bundle_repair_target")
    with pytest.raises(ValueError, match="required"):
        save_generated_code(context)


def test_optional_companions_cannot_override_an_explicit_foreign_owner():
    page = _task("pages", "AppSchemaAgent", ["app.json"], task_type="page_bundle")
    persistence = _task("data", "DatabaseAgent", ["data/contract.json"], task_type="persistence_contract")
    assert "data/contract.json" not in task_allowed_paths(page, [page, persistence])


def test_final_snapshot_digest_covers_contents_plan_evidence_and_binding():
    context = _context()
    files = context["generated_files"]
    original = artifact_snapshot_digest(context, files)
    assert original == artifact_snapshot_digest(deepcopy(context), dict(reversed(list(files.items()))))
    assert original != artifact_snapshot_digest(context, {**files, "extra.py": "new"})
    context["app_build_plan"]["pages"] = [{"name": "another"}]
    assert original != artifact_snapshot_digest(context, files)


@pytest.mark.parametrize("mutation", ["inventory", "page_plan"])
def test_final_acceptance_rejects_changed_original_plan_evidence(mutation):
    context = _context()
    context["app_task_batch_results"]["_meta"] = {
        "task_inventory_digest": task_inventory_digest(context["app_task_batch_items"]),
        "input_digests": {"app_build_plan": task_evidence_digest(context["app_build_plan"])},
    }
    assert planned_artifact_diagnostics(context, context["generated_files"]) == []
    if mutation == "inventory":
        context["app_build_plan"]["build_tasks"].pop()
        context["app_task_batch_items"].pop()
    else:
        context["app_build_plan"]["pages"] = [{"name": "different_page"}]
    assert planned_artifact_diagnostics(context, context["generated_files"])[0]["code"] == "PLAN_INVENTORY_INVALID"


def test_accepted_output_mutation_cannot_preserve_execution_acceptance():
    context = _context()
    results = context["app_task_batch_results"]
    results["_meta"] = {
        "evidence_version": 1,
        "accepted_output_digests": {task_id: task_evidence_digest(output) for task_id, output in results.items()},
    }
    assert planned_artifact_diagnostics(context, context["generated_files"]) == []
    results["models"]["code_files"][0]["content"] = "foreign replacement"
    assert planned_artifact_diagnostics(context, context["generated_files"])[0]["code"] == "TASK_EVIDENCE_INVALID"


def _declared_appgenerator_bridge(data):
    from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
    from mozaiksai.core.workflow.context.authority import (
        DETERMINISTIC_TOOL_WRITER,
        ScopedContextWriter,
        build_context_authority_policy,
    )
    from mozaiksai.core.workflow.context.schema import load_context_variables_config

    workflow = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator"
    config = load_context_variables_config(yaml.safe_load(
        (workflow / "context_variables.yaml").read_text(encoding="utf-8"),
    ))
    transitions = yaml.safe_load((workflow / "transition_graph.yaml").read_text(encoding="utf-8"))
    policy = build_context_authority_policy(
        workflow_name="AppGenerator", definitions=config.definitions,
        transition_rules=transitions["transition_rules"],
    )
    run = ("AppGenerator", data.get("app_id", "test-app"), data.get("chat_id", "test-chat"))
    bridge = ContextVariablesBridge(data, authority_policy=policy)
    bridge._bind_run(run, policy)
    return bridge, ScopedContextWriter(policy, DETERMINISTIC_TOOL_WRITER), policy, run


@pytest.mark.asyncio
async def test_acceptance_repair_policy_and_code_save_use_frozen_context_and_declared_authority(tmp_path):
    from factory_app.workflows.AppGenerator.tools.app_validation import (
        run_app_bundle_acceptance_gate,
    )
    from mozaiksai.core.workflow.agents.factory import _workflow_tool_invocation
    from mozaiksai.core.workflow.context.authority import ContextAuthorityError
    from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
    from tests.test_appplan_materialization_acceptance import _materialize_plan_bundle

    files, _, _, generated, _ = await _materialize_plan_bundle(tmp_path=tmp_path)
    path = "modules/reports/backend/handler.py"
    broken = {**files, path: files[path].replace("class ReportsModule:", "class WrongHandler:")}
    initial = {**generated.data, "generated_files": broken,
               "code_files": [{"filename": name, "content": content} for name, content in broken.items()]}
    bridge, writer, policy, run = _declared_appgenerator_bridge(initial)
    original_results = bridge.snapshot()["app_task_batch_results"]
    assert isinstance(bridge.get("app_build_plan"), MappingProxyType)
    assert isinstance(bridge.get("app_build_plan")["build_tasks"], tuple)
    with pytest.raises(TypeError):
        bridge.get("app_build_plan")["build_tasks"][0]["task_id"] = "foreign"

    with _workflow_tool_invocation(bridge):
        writer.set(bridge, "generated_files", broken)
        with pytest.raises(ContextAuthorityError):
            writer.set(bridge, "app_task_batch_results", {})
        failed = await run_app_bundle_acceptance_gate(context_variables=bridge)
        assert failed["passed"] is False
        assert failed["bundle_repair"]["target_agent"] == "ServiceAgent"
        assert bridge.get("bundle_repair_result")["active"]["task_id"] == "task_reports_services"
        assert isinstance(bridge.get("bundle_repair_result")["active"], MappingProxyType)
        saved = save_generated_code(StructuredOutputOverlay(bridge, {
            "code_files": [{"filename": path, "content": files[path]}],
        }))
        assert saved["saved_files"] == [path]
        assert bridge.get("bundle_repair_result")["active"]["status"] == "responded"
        accepted = await run_app_bundle_acceptance_gate(context_variables=bridge)
        assert accepted["passed"] is True, accepted

    assert bridge.snapshot()["app_task_batch_results"] == original_results
    updates = bridge.consume_authorized_context_updates(policy=policy, run_identity=run)
    assert {"code_files", "bundle_repair_result", "app_bundle_acceptance_result"}.issubset(updates["set"])
    assert "structured_output" not in bridge.snapshot()
    assert "app_task_batch_results" not in updates["set"]


def test_schema_repair_uses_frozen_scope_and_preserves_deterministic_scaffolds(tmp_path, monkeypatch):
    from mozaiksai.core.workflow.agents.factory import _workflow_tool_invocation
    from tests.test_appgenerator_save_app_schema import (
        _base_manifest,
        _base_page,
        _page_repair_context,
        save_app_schema_module,
    )

    seeded = _page_repair_context(tmp_path, monkeypatch)
    bridge, writer, policy, run = _declared_appgenerator_bridge(seeded.data)
    before = bridge.snapshot()
    assert isinstance(bridge.get("bundle_repair_result")["active"], MappingProxyType)
    with _workflow_tool_invocation(bridge):
        writer.set(bridge, "bundle_repair_result", before["bundle_repair_result"])
        result = save_app_schema_module.save_app_schema(
            manifest=_base_manifest(), pages=[{**_base_page(), "title": "Scoped repair"}],
            context_variables=bridge,
        )
    assert "repair rejected" not in result
    assert bridge.get("bundle_repair_result")["active"]["status"] == "responded"
    assert bridge.get("generated_files")["provenance.yaml"] == before["generated_files"]["provenance.yaml"]
    assert bridge.get("generated_files")["config/ai.json"] == before["generated_files"]["config/ai.json"]
    updates = bridge.consume_authorized_context_updates(policy=policy, run_identity=run)
    assert {"generated_files", "code_files", "bundle_repair_result"}.issubset(updates["set"])
