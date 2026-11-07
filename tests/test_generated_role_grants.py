"""Approved design roles compile consistently at generation and refinement boundaries."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.design_access_policy import access_policy_sha256
from mozaiksai.core.workflow.generator_support.module_role_grants import validate_access_policy
from mozaiksai.core.workflow.generator_support.module_write_actions import (
    auth_contract_scopes,
    close_module_actions,
    materialize_module_actions,
    materialize_module_write_implementations,
)
from tests.test_appgenerator_module_write_actions import (
    BACKEND,
    MANIFEST,
    MODULE,
    WRITES,
    _contract,
    _output,
    _plan,
)


def _policy():
    return {
        "roles": [{"role_id": "coordinator", "label": "Coordinator", "description": "Maintains shared tasks."}],
        "grants": [{"role_id": "coordinator", "collection": "tasks", "actions": list(WRITES)}],
    }


def _surface():
    return {"surfaces": [{"surface_id": MODULE, "surface_kind": "module", "owner": "app", "owned_mutations": []}]}


def _arguments():
    return {"app_build_plan": _plan(), "data_contract": _contract("app_wide"),
            "design_surface_map": _surface(), "access_policy": _policy()}


def _closed():
    # Protected app-wide reads retain their independently declared access contract.
    baseline = close_module_actions(_output(), app_build_plan=_plan(), data_contract=_contract("app_wide"))
    reads = [action for action in baseline["module_contract"]["module_yaml"]["actions"] if action["id"] not in WRITES]
    return close_module_actions(_output(reads), **_arguments())


@pytest.mark.parametrize("mutation, error", [
    (lambda p: p["roles"].append(deepcopy(p["roles"][0])), "repeats a role"),
    (lambda p: p["grants"][0].update(role_id="stranger"), "unknown role"),
    (lambda p: p["grants"][0].update(collection="other"), "unknown app-owned collection"),
    (lambda p: p["grants"][0]["actions"].append("unknown_action"), "not an approved mutation"),
    (lambda p: p["grants"][0]["actions"].pop(), "omits canonical write"),
    (lambda p: p["grants"].append(deepcopy(p["grants"][0])), "repeats grant"),
])
def test_policy_rejects_unresolved_ambiguous_or_incomplete_grants(mutation, error):
    policy = _policy()
    mutation(policy)
    with pytest.raises(ValueError, match=error):
        validate_access_policy(policy, data_contract=_contract("app_wide"), design_surface_map=_surface())


def test_policy_cannot_grant_managed_facade_actions():
    surface = _surface()
    surface["surfaces"][0]["owner"] = "hosted"
    with pytest.raises(ValueError, match="unknown app-owned collection"):
        validate_access_policy(_policy(), data_contract=_contract("app_wide"), design_surface_map=surface)


def test_approved_roles_close_shared_writes_and_compile_real_implementations():
    closed = _closed()
    manifest = closed["module_contract"]["module_yaml"]
    actions = {action["id"]: action for action in manifest["actions"]}
    for action in WRITES:
        assert actions[action]["permissions"] == [f"tasks.{action}"]
        assert actions[action]["api_surface"] is None
        assert actions[action]["handler_method"] == action
    assert {item["id"] for item in manifest["permissions"]} == {f"tasks.{action}" for action in WRITES}
    assert close_module_actions(closed, **_arguments()) == closed
    files = extract_code_file_map_from_payload(closed)
    compiled = materialize_module_write_implementations(files, **_arguments())
    for layer in ("handler", "service", "repo"):
        for action in WRITES:
            assert f"async def {action}(" in compiled[f"{BACKEND}/{layer}.py"]
    assert MANIFEST not in materialize_module_actions(files, **_arguments())


def test_auth_scaffold_compiles_grants_without_giving_every_user_their_union():
    files = {"app.json": json.dumps({"authRequired": False})}
    args = {k: v for k, v in _arguments().items() if k != "app_build_plan"}
    rendered = materialize_auth_scaffold(files, **args)
    auth = yaml.safe_load(rendered["config/auth.yaml"])
    assert json.loads(rendered["app.json"])["authRequired"] is True
    assert auth["schema_version"] == "mozaiks.auth.v2"
    assert auth["authorization"]["roles"] == [{"id": "coordinator", "permissions": sorted(f"tasks.{a}" for a in WRITES)}]
    assert auth_contract_scopes(rendered, include_role_grants=False) == frozenset({"openid", "profile", "email"})
    assert auth_contract_scopes(rendered) == frozenset({"openid", "profile", "email", *(f"tasks.{a}" for a in WRITES)})
    files.update(rendered)
    auth["routes"]["post_login_default"] = "/tasks"
    auth["frontend"]["default_scopes"].append("calendar.read")
    files["config/auth.yaml"] = yaml.safe_dump(auth)
    args["access_policy"] = {"roles": [], "grants": []}
    revised = yaml.safe_load(materialize_auth_scaffold(files, **args)["config/auth.yaml"])
    assert revised["authorization"]["roles"] == []
    assert revised["routes"]["post_login_default"] == "/tasks"
    assert "calendar.read" in revised["frontend"]["default_scopes"]


def test_candidate_cannot_self_grant_even_alongside_an_approved_policy():
    candidate = {"code_files": [{"filename": "config/auth.yaml", "content": "authorization: {roles: []}"}]}
    with pytest.raises(ValueError, match="cannot author config/auth.yaml"):
        close_module_actions(candidate, **_arguments())


def _reviewed_context(policy, *, files=None):
    binding = {"build_registry_id": "appreg-test", "target_app_id": "test-app", "build_id": "build-test", "phase": "genesis"}
    return {
        "run_build_binding": binding, "data_contract": _contract("app_wide"), "design_surface_map": _surface(),
        "generated_files": files or {},
        "design_docs_artifact": {
            "app_id": "test-app", "build_family": "design_docs", "build_key": "design_docs",
            "source_workflow": "DesignDocs", "lifecycle_status": "current",
            "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": {
                "data_contract": _contract("app_wide"), "surface_map": _surface(), "access_policy": policy,
                "access_policy_review": {"policy_sha256": access_policy_sha256(policy), "target_app_id": "test-app",
                                         "build_id": "build-test", "status": "approved", "review_id": "review-test", "reviewed_by": "owner"},
            }}},
        },
    }


def test_final_assembly_compiles_approved_grants_and_rejects_unreviewed_role_edits():
    files = {**extract_code_file_map_from_payload(_closed()), "app.json": '{"authRequired": true}'}
    args = {k: v for k, v in _arguments().items() if k != "access_policy"}
    outputs = [{"code_files": [{"filename": p, "content": c} for p, c in files.items()]}]
    assembled = {entry["filename"]: entry["content"] for entry in _merge_code_files(
        outputs, **args, context_variables=_reviewed_context(_policy()),
    )}
    assert yaml.safe_load(assembled["config/auth.yaml"])["authorization"]["roles"][0]["id"] == "coordinator"
    # A final feature output cannot introduce auth roles by skipping task admission.
    ungranted = yaml.safe_load(assembled["config/auth.yaml"])
    ungranted["authorization"] = {"roles": []}
    with pytest.raises(ValueError, match="cannot author config/auth.yaml"):
        _merge_code_files([{"code_files": [{"filename": p, "content": assembled[p]} for p in ("app.json", "config/auth.yaml")]}],
                          context_variables={"generated_files": assembled | {"config/auth.yaml": yaml.safe_dump(ungranted)}})
    # Removing a grant cannot leave existing actions apparently validated against the old auth file.
    empty = {"roles": [], "grants": []}
    with pytest.raises(ValueError, match="unresolved permissions"):
        _merge_code_files(outputs, **args, context_variables=_reviewed_context(empty, files=assembled))


@pytest.mark.parametrize("reviewed", [False, True])
def test_final_assembly_cannot_launder_role_grants_into_every_users_default_scopes(reviewed):
    baseline = {"app.json": '{"authRequired": true}'}
    baseline.update(materialize_auth_scaffold(baseline))
    candidate = deepcopy(baseline)
    auth = yaml.safe_load(candidate["config/auth.yaml"])
    auth["frontend"]["default_scopes"].extend(f"tasks.{action}" for action in WRITES)
    candidate["config/auth.yaml"] = yaml.safe_dump(auth)
    context = _reviewed_context(_policy(), files=baseline) if reviewed else {"generated_files": baseline}
    with pytest.raises(ValueError, match="cannot author config/auth.yaml"):
        _merge_code_files(
            [{"code_files": [{"filename": path, "content": content} for path, content in candidate.items()]}],
            context_variables=context, data_contract=_contract("app_wide") if reviewed else None,
            design_surface_map=_surface() if reviewed else None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["forged_review", "remapped_collection", "forged_auth", "forged_plan"])
async def test_task_local_context_cannot_reauthorize_its_output(monkeypatch, attack):
    context = _reviewed_context(_policy())
    context["app_build_plan"] = _plan()
    if attack == "forged_review":
        embedded = {"design_docs_artifact": context.pop("design_docs_artifact"), "access_policy": _policy()}
    elif attack in {"forged_auth", "forged_plan"}:
        context.pop("design_docs_artifact")
        auth = {"frontend": {"default_scopes": [f"tasks.{action}" for action in WRITES]}}
        embedded = {"generated_files": {"config/auth.yaml": yaml.safe_dump(auth)}}
        if attack == "forged_plan":
            embedded["app_build_plan"] = {"capability_packs": [], "pages": []}
    else:
        altered = deepcopy(context["data_contract"])
        altered["surfaces"][0]["collections"][0]["tenancy"] = "per_user"
        altered["surfaces"][0]["collections"][0]["owner_field"] = "user_id"
        embedded = {"data_contract": altered}
    task = {"task_id": "contract", "task_type": "module_contract", "capability_pack_id": MODULE,
            "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare the module.",
            "owned_paths": [MANIFEST], "depends_on": [], "context_variables": embedded}
    context["app_build_plan"]["build_tasks"] = [task]

    async def run(_runner, _request):
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=_closed())

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app/workflows",
    )
    with pytest.raises(task_batches._TaskRejected) as rejected:
        await task_batches._run_one_task(
            workflow_name="AppGenerator", batch=next(b for b in config.batches if b.id == "app_build_tasks"),
            task=task, all_task_items=[task], base_context=context, completed_task_outputs={}, current_batch_outputs={},
            agents={"ConfigMiddlewareAgent": object()}, chat_id="roles", app_id="test-app", user_id="owner",
            semaphore=asyncio.Semaphore(1), fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
        )
    result = rejected.value.evidence
    assert result["recoverable"] is True, result
    assert "error" in result and "module_contract" not in result
    assert ("cannot replace data_contract" if attack == "remapped_collection" else "not constructed open") in result["error"]
