"""Generated custom actions resolve permissions before task or repair admission."""
from __future__ import annotations

import asyncio
import importlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.composition import (
    ExecutorRegistry,
    ModuleActionDispatchRequest,
    ModuleDispatchAuthority,
    ModuleDispatchScope,
    ModuleExecutor,
    dispatch_module_action,
)
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_write_actions import (
    close_module_actions,
    materialize_module_actions,
)
from mozaiksai.core.workflow.outputs.runtime_validation import validate_agent_structured_output

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/task_output_syntax_ed0d00b0.json").read_text(encoding="utf-8"))
TASK_ID = "task_tasks_management_module_contract"
MODULE = "tasks_management"
ACTION = "summarize_tasks"
MANIFEST = f"modules/{MODULE}/module.yaml"
AUTH_PATH = "config/auth.yaml"


def _auth(*scopes):
    return {AUTH_PATH: yaml.safe_dump({"frontend": {"default_scopes": list(scopes)}})}


def _plan(strategy="public", source="generated_module"):
    return {
        "auth_strategy": strategy, "roles": ["admin"] if strategy == "role-based" else [],
        "capability_packs": [{"capability_pack_id": MODULE, "capability_source": source, "primary_entities": ["Task"]}],
        "pages": [],
    }


def _payload(*permissions):
    return {"module_contract": {"module_id": MODULE, "module_yaml": {
        "schema_version": "mozaiks.module.v1",
        "module": {"id": MODULE, "handler": "backend.handler:TasksHandler"},
        "permissions": [{"id": value, "description": "Declaration is not a grant."} for value in permissions],
        "actions": [{"id": ACTION, "handler_method": ACTION, "permissions": list(permissions)}],
    }}}


def _permissions(payload):
    return next(action["permissions"] for action in payload["module_contract"]["module_yaml"]["actions"]
                if action["id"] == ACTION)


def _candidate(permission):
    registry = task_batches._structured_registry_for_agent("AppGenerator", "ConfigMiddlewareAgent")
    model = registry["ConfigMiddlewareAgent"]
    candidate = {key: deepcopy(value) for key, value in FIXTURE["dependency_task_outputs"][TASK_ID].items()
                 if key in model.model_fields}
    # Recorded task results contain rendered CRUD, events and entitlement gates.
    # Reconstruct a raw current-schema custom action; code owns those derived fields.
    action = next(action for action in candidate["module_contract"]["module_yaml"]["actions"]
                  if action["id"] == ACTION)
    action.pop("entitlement_gate", None)
    action["permissions"] = [permission]
    candidate["module_contract"]["module_yaml"]["actions"] = [action]
    candidate["module_contract"]["events_yaml"] = None
    candidate["code_files"] = []
    result = validate_agent_structured_output(
        agent_name="ConfigMiddlewareAgent", reply=json.dumps(candidate), structured_registry=registry,
    )
    assert result is not None and result.validation_passed, result.error if result else "missing model"
    return result.structured_data


def _context():
    return {**deepcopy(FIXTURE["context"]), "generated_files": _auth("openid", "profile", "email", "tasks.view")}


async def _run_task(context, *, prior_failure=None):
    config = task_batches.load_task_batches_config("AppGenerator", workflows_root=ROOT / "factory_app/workflows")
    task = deepcopy(next(item for item in FIXTURE["task_inventory"] if item["task_id"] == TASK_ID))
    return await task_batches._run_one_task(
        workflow_name="AppGenerator", batch=next(batch for batch in config.batches if batch.id == "app_build_tasks"),
        task=task, all_task_items=deepcopy(FIXTURE["task_inventory"]), base_context=context,
        completed_task_outputs={}, current_batch_outputs={}, agents={"ConfigMiddlewareAgent": object()},
        chat_id="custom-action-authorization", app_id="custom-action-authorization", user_id="user-1",
        semaphore=asyncio.Semaphore(1), fresh_agents_per_task=False, agents_factory=None,
        context_authority_policy=None, prior_failure=prior_failure, recovery_episode=prior_failure is not None,
    )


def test_unresolved_custom_permission_reports_choices_without_mutating_candidate():
    candidate = _payload("tasks.veiw")
    before = deepcopy(candidate)
    with pytest.raises(ValueError) as rejected:
        close_module_actions(candidate, app_build_plan=_plan(), companion_files=_auth("tasks.view"))
    assert all(value in str(rejected.value) for value in (MODULE, ACTION, "tasks.veiw", "tasks.view"))
    assert candidate == before


def test_known_custom_permission_is_preserved():
    candidate = _payload("tasks.view")
    closed = close_module_actions(candidate, app_build_plan=_plan(), companion_files=_auth("tasks.view"))
    assert _permissions(closed) == ["tasks.view"]
    assert _permissions(candidate) == ["tasks.view"]


@pytest.mark.parametrize("action_id", ["create_task", "update_task", "delete_task", "get_tasks", "list_tasks"])
def test_canonical_looking_name_without_owned_collection_cannot_bypass_validation(action_id):
    candidate = _payload("tasks.veiw")
    candidate["module_contract"]["module_yaml"]["actions"][0]["id"] = action_id
    with pytest.raises(ValueError) as rejected:
        close_module_actions(candidate, app_build_plan=_plan(), companion_files=_auth("tasks.view"))
    assert action_id in str(rejected.value) and "tasks.veiw" in str(rejected.value)


@pytest.mark.parametrize(("action_id", "restriction"), [
    (action, restriction) for action in ("create_task", "update_task", "delete_task")
    for restriction in ("internal", "entitlement")
] + [("get_tasks", "authored_read"), ("list_tasks", "authored_read")])
def test_preserved_app_wide_access_cannot_hide_unresolved_permissions(action_id, restriction):
    candidate = _payload()
    manifest = candidate["module_contract"]["module_yaml"]
    manifest["actions"] = [{
        "id": name, "handler_method": name, "permissions": [], "api_surface": "internal",
        "input_schema": {"type": "object", "properties": []},
        "output_schema": {"type": "object", "properties": []},
    } for name in ("create_task", "update_task", "delete_task", "get_tasks", "list_tasks")]
    action = next(action for action in manifest["actions"] if action["id"] == action_id)
    action["permissions"] = ["tasks.veiw"]
    subscription = None
    if restriction == "entitlement":
        action["api_surface"] = None
        action["entitlement_gate"] = "feature.task_write"
        subscription = {"module_contract_updates": [{
            "module_id": MODULE, "action_id": action_id, "entitlement_gate": "feature.task_write",
        }]}
    elif restriction == "authored_read":
        action["api_surface"] = None
    contract = deepcopy(FIXTURE["context"]["data_contract"])
    for surface in contract["surfaces"]:
        for collection in surface["collections"]:
            collection["tenancy"] = "app_wide"
            collection["owner_field"] = None
    before = deepcopy(candidate)
    with pytest.raises(ValueError) as rejected:
        close_module_actions(candidate, app_build_plan=_plan(), data_contract=contract,
                             companion_files=_auth("tasks.view"), subscription_contract=subscription)
    assert action_id in str(rejected.value) and "tasks.veiw" in str(rejected.value)
    assert candidate == before


@pytest.mark.parametrize("source", ["operator_extension", "operator_pack", "framework_pack"])
def test_operator_and_pack_permissions_are_not_reinterpreted_as_generated_auth(source):
    candidate = _payload("operator.tasks.read")
    closed = close_module_actions(candidate, app_build_plan=_plan(source=source))
    assert _permissions(closed) == ["operator.tasks.read"]


@pytest.mark.parametrize("strategy", ["basic-login", "role-based", "third-party"])
def test_missing_auth_uses_only_canonical_scaffold_scopes(strategy):
    closed = close_module_actions(_payload("openid"), app_build_plan=_plan(strategy))
    assert _permissions(closed) == ["openid"]
    with pytest.raises(ValueError) as rejected:
        close_module_actions(_payload("admin"), app_build_plan=_plan(strategy))
    assert all(scope in str(rejected.value) for scope in ("openid", "profile", "email"))


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
def test_owned_data_derives_scaffold_auth_without_model_auth_intent(tenancy):
    contract = deepcopy(FIXTURE["context"]["data_contract"])
    for surface in contract["surfaces"]:
        for collection in surface["collections"]:
            collection["tenancy"] = tenancy
            collection["owner_field"] = "user_id" if tenancy == "per_user" else "workspace_id"
            if tenancy == "per_workspace":
                for field in collection["fields"]:
                    if field["name"] == "user_id":
                        field["name"] = "workspace_id"
    closed = close_module_actions(_payload("openid"), app_build_plan=_plan(), data_contract=contract)
    assert _permissions(closed) == ["openid"]
    with pytest.raises(ValueError, match="tasks.view"):
        close_module_actions(_payload("tasks.view"), app_build_plan=_plan(), data_contract=contract)


@pytest.mark.parametrize("auth", [{}, _auth(), _auth("tasks.read")])
def test_public_or_explicit_auth_does_not_invent_scaffold_grants(auth):
    with pytest.raises(ValueError, match="openid"):
        close_module_actions(_payload("openid"), app_build_plan=_plan(), companion_files=auth)


@pytest.mark.parametrize("typed", [True, False])
def test_candidate_auth_cannot_approve_its_own_permission(typed):
    payload = _payload("tasks.veiw")
    if not typed:
        payload = {"code_files": [{"filename": path, "content": content}
                                  for path, content in extract_code_file_map_from_payload(payload).items()]}
    payload.setdefault("code_files", []).append({"filename": AUTH_PATH, "content": _auth("tasks.veiw")[AUTH_PATH]})
    with pytest.raises(ValueError, match="tasks.veiw"):
        close_module_actions(payload, app_build_plan=_plan(), companion_files=_auth("tasks.view"))


@pytest.mark.asyncio
async def test_task_rejection_retains_candidate_and_one_recovery_can_preserve_known_restriction(monkeypatch):
    candidate = _candidate("tasks.veiw")
    context = _context()
    original_context = deepcopy(context)
    requests = []

    async def run(_runner, request):
        requests.append(request)
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=deepcopy(candidate))

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    with pytest.raises(task_batches._TaskRejected) as rejected:
        await _run_task(context)
    failure = rejected.value.evidence
    assert failure["failure_kind"] == "output_rejected" and failure["recoverable"] is True
    assert failure["attempts"] == 1 and failure["rejected_output"] == candidate
    assert all(value in failure["error"] for value in (MODULE, ACTION, "tasks.veiw", "tasks.view"))
    assert len(requests) == 1 and context == original_context

    candidate = _candidate("tasks.view")
    accepted = await _run_task(context, prior_failure=failure)
    assert _permissions(accepted) == ["tasks.view"]
    assert len(requests) == 2 and "tasks.veiw" in requests[1].prompt
    assert context == original_context


@pytest.mark.parametrize("permissions", ["tasks.view", [{"id": "tasks.view"}], ["tasks.view", 7]])
@pytest.mark.asyncio
async def test_raw_malformed_permissions_are_recoverable_task_rejections(monkeypatch, permissions):
    payload = _payload()
    payload["module_contract"]["module_yaml"]["actions"][0]["permissions"] = deepcopy(permissions)
    before = deepcopy(payload)
    with pytest.raises(ValueError) as rejected:
        close_module_actions(payload, app_build_plan=_plan(), companion_files=_auth("tasks.view"))
    assert "permissions" in str(rejected.value) and payload == before

    # Raw YAML in the code_files lane is valid worker structured output, while
    # its runtime action contract is malformed and must remain repairable.
    candidate = _candidate("tasks.view")
    candidate["module_contract"] = None
    candidate["code_files"] = [{"filename": path, "content": content}
                               for path, content in extract_code_file_map_from_payload(payload).items()]
    validated = validate_agent_structured_output(
        agent_name="ConfigMiddlewareAgent", reply=candidate,
        structured_registry=task_batches._structured_registry_for_agent("AppGenerator", "ConfigMiddlewareAgent"),
    )
    assert validated is not None and validated.validation_passed, validated.error if validated else "missing model"
    candidate = validated.structured_data
    calls = []

    async def run(_runner, request):
        calls.append(request.task_id)
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=deepcopy(candidate))

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    with pytest.raises(task_batches._TaskRejected) as rejected:
        await _run_task(_context())
    failure = rejected.value.evidence
    assert failure["failure_kind"] == "output_rejected" and failure["recoverable"] is True
    assert failure["rejected_output"] == candidate and calls == [TASK_ID]


def test_repair_save_rejects_unchanged_permission_without_changing_admitted_files():
    candidate = _candidate("tasks.veiw")
    admitted = close_module_actions(
        _candidate("tasks.view"), app_build_plan=FIXTURE["context"]["app_build_plan"],
        data_contract=FIXTURE["context"]["data_contract"], companion_files=_auth("tasks.view"),
    )
    files = extract_code_file_map_from_payload(admitted)
    context = ContextVariablesBridge({
        **_context(), "app_task_batch_items": deepcopy(FIXTURE["task_inventory"]),
        "app_task_batch_results": {TASK_ID: admitted},
        "generated_files": {**_auth("tasks.view"), **files},
        "code_files": [{"filename": path, "content": content} for path, content in files.items()],
        "deleted_files": [],
    })
    repair = prepare_bundle_repair({"passed": False, "diagnostics": [{
        "path": MANIFEST, "error": "Custom action permission requires correction.",
    }]}, context)
    assert repair["target_agent"] == "ConfigMiddlewareAgent" and repair["active"]["task_id"] == TASK_ID
    before = context.snapshot()
    result = save_generated_code(StructuredOutputOverlay(context, candidate))
    assert result["status"] == "rejected" and "tasks.veiw" in result["error"]
    after = context.snapshot()
    for key in ("generated_files", "code_files", "deleted_files", "app_task_batch_results"):
        assert after[key] == before[key]
    assert after["bundle_repair_result"]["active"]["status"] == "rejected"


@pytest.mark.parametrize("boundary", ["materializer", "assembly"])
def test_final_bundle_cannot_reintroduce_unresolved_custom_permission(boundary):
    files = {**_auth("tasks.view"), **extract_code_file_map_from_payload(_payload("tasks.veiw"))}
    before = deepcopy(files)
    with pytest.raises(ValueError, match="tasks.veiw"):
        if boundary == "materializer":
            materialize_module_actions(files, app_build_plan=_plan())
        else:
            _merge_code_files([{"code_files": [{"filename": path, "content": content}
                                               for path, content in files.items()]}], app_build_plan=_plan())
    assert files == before


@pytest.mark.asyncio
async def test_approved_permission_still_requires_actual_dispatch_authority(monkeypatch):
    admitted = close_module_actions(_payload("tasks.view"), app_build_plan=_plan(), companion_files=_auth("tasks.view"))

    class Handler:
        calls = 0

        async def summarize_tasks(self, ctx, **params):
            self.calls += 1
            return {"summary": "synthetic"}

    class Audit:
        async def log_module_action(self, **kwargs):
            return None

    handler = Handler()
    executor = ModuleExecutor()
    executor.register(MODULE, handler, action_method_map={ACTION: ACTION}, action_permissions={ACTION: _permissions(admitted)})
    registry = ExecutorRegistry()
    registry.register(executor)
    app = SimpleNamespace(state=SimpleNamespace(executor_registry=registry))
    executor_module = importlib.import_module("mozaiksai.core.runtime.composition.module_executor")
    monkeypatch.setattr(executor_module, "get_audit_logger", lambda: Audit())
    for scopes, allowed in [((), False), (("openid", "profile", "email"), False), (("tasks.view",), True)]:
        result = await dispatch_module_action(ModuleActionDispatchRequest(
            module=MODULE, action=ACTION,
            scope=ModuleDispatchScope(app_id="generated-auth-test", user_id="user-1"),
            authority=ModuleDispatchAuthority(kind="app_internal", permission_mode="enforce",
                reason="generated permission regression", actor_id="user-1", permissions=scopes),
        ), app=app)
        assert result.success is allowed
        if not allowed:
            assert result.error_code == "PERMISSION_DENIED" and handler.calls == 0
    assert handler.calls == 1
