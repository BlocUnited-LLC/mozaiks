"""Approved design roles compile consistently at generation and refinement boundaries."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.app.auth_contract import AppAuthContractError
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.design_access_policy import access_policy_sha256
from mozaiksai.core.workflow.generator_support.module_role_grants import (
    approved_module_role_permissions,
    compiled_role_grants,
    validate_access_policy,
)
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
    return {"surfaces": [{"surface_id": MODULE, "surface_kind": "module", "owner": "app",
                          "primary_entities": ["Task"], "owned_mutations": []}]}


def _arguments():
    return {"app_build_plan": _plan(), "data_contract": _contract("app_wide"),
            "design_surface_map": _surface(), "access_policy": _policy()}


def _closed():
    # Protected app-wide reads retain their independently declared access contract.
    baseline = close_module_actions(_output(), app_build_plan=_plan(), data_contract=_contract("app_wide"))
    reads = [action for action in baseline["module_contract"]["module_yaml"]["actions"] if action["id"] not in WRITES]
    return close_module_actions(_output(reads), **_arguments())


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
def test_explicit_read_grants_survive_canonical_closure_and_file_roundtrip(tenancy):
    policy = _policy()
    policy["grants"][0]["actions"].extend(["get_tasks", "list_tasks"])
    args = {**_arguments(), "data_contract": _contract(tenancy), "access_policy": policy}
    closed = close_module_actions(_output(), **args)
    actions = {action["id"]: action for action in closed["module_contract"]["module_yaml"]["actions"]}
    for action in (*WRITES, "get_tasks", "list_tasks"):
        assert actions[action]["permissions"] == [f"tasks.{action}"]
    assert actions["get_tasks"]["handler_method"] == "get_tasks"
    assert actions["get_tasks"]["api_surface"] is None
    assert close_module_actions(closed, **args) == closed
    files = extract_code_file_map_from_payload(closed)
    assert materialize_module_actions(files, **args) == {}
    auth = materialize_auth_scaffold(
        {**files, "app.json": '{"authRequired": true}'},
        **{key: value for key, value in args.items() if key != "app_build_plan"},
    )
    assert yaml.safe_load(auth["config/auth.yaml"])["authorization"]["roles"] == compiled_role_grants(policy)
    assert auth_contract_scopes(auth, include_role_grants=False) == frozenset({"openid", "profile", "email"})


@pytest.mark.parametrize("read", ["get_tasks", "list_tasks"])
def test_selecting_one_canonical_read_requires_the_other_without_inventing_grants(read):
    policy = _policy()
    policy["grants"][0]["actions"].append(read)
    before = deepcopy(policy)
    missing = "list_tasks" if read == "get_tasks" else "get_tasks"
    with pytest.raises(ValueError, match=f"omits canonical read grants.*{missing}"):
        validate_access_policy(policy, data_contract=_contract(), design_surface_map=_surface())
    assert policy == before


def test_read_pair_coverage_is_across_roles_without_giving_each_role_both():
    policy = _policy()
    policy["grants"][0]["actions"].append("list_tasks")
    policy["roles"].append({"role_id": "reader", "label": "Reader", "description": "Reads one task."})
    policy["grants"].append({"role_id": "reader", "collection": "tasks", "actions": ["get_tasks"]})
    validated = validate_access_policy(policy, data_contract=_contract(), design_surface_map=_surface())
    assert validated == policy
    roles = {entry["id"]: entry["permissions"] for entry in compiled_role_grants(validated)}
    assert roles["reader"] == ["tasks.get_tasks"]
    assert "tasks.get_tasks" not in roles["coordinator"]


@pytest.mark.parametrize("reads", [["get_tasks", "list_tasks"], ["task_summary"]])
def test_explicit_reads_do_not_bypass_existing_mutable_collection_write_coverage(reads):
    policy = _policy()
    policy["grants"][0]["actions"] = reads
    surface = _surface()
    surface["surfaces"][0]["custom_reads"] = ["task_summary"]
    with pytest.raises(ValueError, match="omits canonical write grants"):
        validate_access_policy(policy, data_contract=_contract(), design_surface_map=surface)


@pytest.mark.parametrize("write_mode", ["workflow_write", "platform_sync"])
def test_read_only_collections_can_grant_reads_without_inventing_writes(write_mode):
    policy = _policy()
    policy["grants"][0]["actions"] = ["get_tasks", "list_tasks"]
    args = {**_arguments(), "data_contract": _contract("per_user", write_mode=write_mode), "access_policy": policy}
    closed = close_module_actions(_output(), **args)
    actions = closed["module_contract"]["module_yaml"]["actions"]
    assert {action["id"] for action in actions} == {"get_tasks", "list_tasks"}
    assert all(action["permissions"] == [f"tasks.{action['id']}"] for action in actions)


@pytest.mark.parametrize("canonical_reads", [False, True])
def test_custom_read_grant_is_exact_and_never_inherits_into_raw_reads(canonical_reads):
    policy = _policy()
    policy["grants"][0]["actions"].append("task_summary")
    if canonical_reads:
        policy["grants"][0]["actions"].extend(["get_tasks", "list_tasks"])
    surface = _surface()
    surface["surfaces"][0]["custom_reads"] = ["task_summary"]
    args = {**_arguments(), "data_contract": _contract(), "access_policy": policy, "design_surface_map": surface}
    custom = {"id": "task_summary", "handler_method": "summarize", "permissions": ["invented"],
              "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}}
    closed = close_module_actions(_output([custom]), **args)
    actions = {action["id"]: action for action in closed["module_contract"]["module_yaml"]["actions"]}
    assert actions["task_summary"] == {**custom, "permissions": ["tasks.task_summary"]}
    for read in ("get_tasks", "list_tasks"):
        assert actions[read]["permissions"] == ([f"tasks.{read}"] if canonical_reads else [])
    assert close_module_actions(closed, **args) == closed


def test_custom_read_cannot_claim_multiple_collections_or_a_foreign_modules_read():
    contract = _contract()
    notes = deepcopy(contract["surfaces"][0]["collections"][0])
    notes.update(name="notes", entity="Note", lifecycle={"write_mode": "platform_sync"})
    contract["surfaces"][0]["collections"].append(notes)
    surface = _surface()
    surface["surfaces"][0]["custom_reads"] = ["task_summary"]
    policy = _policy()
    policy["grants"][0]["actions"].append("task_summary")
    policy["grants"].append({"role_id": "coordinator", "collection": "notes", "actions": ["task_summary"]})
    with pytest.raises(ValueError, match="task_summary names multiple collections"):
        validate_access_policy(policy, data_contract=contract, design_surface_map=surface)
    policy["grants"].pop()
    surface["surfaces"][0]["custom_reads"] = []
    surface["surfaces"].append({"surface_id": "other", "surface_kind": "module", "owner": "app",
                                "custom_reads": ["task_summary"]})
    with pytest.raises(ValueError, match="not an approved action"):
        validate_access_policy(policy, data_contract=contract, design_surface_map=surface)


@pytest.mark.parametrize("action", ["get_notes", "list_notes", "create_note"])
def test_custom_inventory_cannot_reanchor_another_collections_canonical_action(action):
    contract = _contract()
    notes = deepcopy(contract["surfaces"][0]["collections"][0])
    notes.update(name="notes", entity="Note")
    contract["surfaces"][0]["collections"].append(notes)
    surface = _surface()
    surface["surfaces"][0]["primary_entities"].append("Note")
    surface["surfaces"][0]["custom_reads"] = ["get_notes", "list_notes"]
    surface["surfaces"][0]["owned_mutations"] = ["create_note"]
    policy = _policy()
    policy["grants"][0]["actions"].append(action)
    with pytest.raises(ValueError, match=f"{action}.*collection 'notes'.*not 'tasks'"):
        validate_access_policy(policy, data_contract=contract, design_surface_map=surface)


@pytest.mark.parametrize("mutation, error", [
    (lambda p: p["roles"].append(deepcopy(p["roles"][0])), "repeats a role"),
    (lambda p: p["grants"][0].update(role_id="stranger"), "unknown role"),
    (lambda p: p["grants"][0].update(collection="other"), "unknown app-owned collection"),
    (lambda p: p["grants"][0]["actions"].append("unknown_action"), "not an approved action"),
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


def test_incomplete_policy_reports_every_collection_for_one_repair():
    contract = _contract("app_wide")
    template = contract["surfaces"][0]["collections"][0]
    collections, grants = [], []
    for name, entity in [("tasks", "Task"), ("notes", "Note"), ("archives", "Archive")]:
        collection = deepcopy(template)
        collection.update(name=name, entity=entity)
        collections.append(collection)
        grants.append({"role_id": "coordinator", "collection": name, "actions": [f"create_{entity.lower()}"]})
    contract["surfaces"][0]["collections"] = collections
    policy = {"roles": _policy()["roles"], "grants": grants}
    before = deepcopy(policy)
    expected = "; ".join([
        "access_policy for 'archives' omits canonical write grants ['delete_archive', 'update_archive']",
        "access_policy for 'notes' omits canonical write grants ['delete_note', 'update_note']",
        "access_policy for 'tasks' omits canonical write grants ['delete_task', 'update_task']",
    ])
    for ordered in (grants, list(reversed(grants))):
        with pytest.raises(ValueError) as error:
            validate_access_policy({**policy, "grants": ordered}, data_contract=contract, design_surface_map=_surface())
        assert str(error.value) == expected
    assert policy == before
    for grant in policy["grants"]:
        entity = grant["actions"][0].removeprefix("create_")
        grant["actions"].extend([f"update_{entity}", f"delete_{entity}"])
    assert validate_access_policy(policy, data_contract=contract, design_surface_map=_surface()) == policy


def test_cross_role_action_bindings_report_all_conflicts_without_selecting_authority():
    contract = _contract("app_wide")
    notes = deepcopy(contract["surfaces"][0]["collections"][0])
    notes.update(name="notes", entity="Note")
    contract["surfaces"][0]["collections"].append(notes)
    surface = _surface()
    mutations = ["archive_records", "restore_records"]
    surface["surfaces"][0]["owned_mutations"] = mutations
    policy = _policy()
    policy["roles"].append({"role_id": "reviewer", "label": "Reviewer", "description": "Reviews tasks."})
    policy["grants"][0]["actions"].extend(mutations)
    policy["grants"].append({"role_id": "reviewer", "collection": "notes", "actions": mutations.copy()})
    before = deepcopy(policy)
    expected = "; ".join([
        f"access_policy action {MODULE}.{action} names multiple collections ['notes', 'tasks']; "
        "bind this module action to exactly one collection in every role's grants, "
        "even when its implementation changes several collections"
        for action in mutations
    ] + ["access_policy for 'notes' omits canonical write grants ['create_note', 'delete_note', 'update_note']"])
    for grants in (policy["grants"], list(reversed(policy["grants"]))):
        with pytest.raises(ValueError) as error:
            validate_access_policy({**policy, "grants": grants}, data_contract=contract, design_surface_map=surface)
        assert str(error.value) == expected
    assert policy == before

    # The author chooses the anchor and missing permissions; validation never does.
    policy["grants"][1]["collection"] = "tasks"
    policy["grants"].append({"role_id": "coordinator", "collection": "notes",
                             "actions": ["create_note", "update_note", "delete_note"]})
    validated = validate_access_policy(policy, data_contract=contract, design_surface_map=surface)
    assert validated == policy
    assert compiled_role_grants(validated)[1] == {
        "id": "reviewer", "permissions": [f"tasks.{action}" for action in mutations],
    }
    permissions = approved_module_role_permissions(policy, data_contract=contract, design_surface_map=surface)
    assert all(permissions[MODULE][action] == f"tasks.{action}" for action in mutations)


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


@pytest.mark.parametrize("content", ["broken: [", "[]", "null", "true"])
def test_reviewed_policy_preserves_invalid_auth_for_gate_diagnostics(content):
    files = {"app.json": json.dumps({"authRequired": True}), "config/auth.yaml": content}
    before = dict(files)
    args = {k: v for k, v in _arguments().items() if k != "app_build_plan"}
    with pytest.raises(AppAuthContractError, match="config/auth.yaml"):
        materialize_auth_scaffold(files, **args)
    assert files == before


def test_candidate_cannot_self_grant_even_alongside_an_approved_policy():
    candidate = {"code_files": [{"filename": "config/auth.yaml", "content": "authorization: {roles: []}"}]}
    with pytest.raises(ValueError, match="cannot author config/auth.yaml"):
        close_module_actions(candidate, **_arguments())


def _reviewed_context(policy, *, files=None, contract=None):
    contract = contract or _contract("app_wide")
    binding = {"build_registry_id": "appreg-test", "target_app_id": "test-app", "build_id": "build-test", "phase": "genesis"}
    return {
        "run_build_binding": binding, "data_contract": contract, "design_surface_map": _surface(),
        "generated_files": files or {},
        "design_docs_artifact": {
            "app_id": "test-app", "build_family": "design_docs", "build_key": "design_docs",
            "source_workflow": "DesignDocs", "lifecycle_status": "current",
            "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": {
                "data_contract": contract, "surface_map": _surface(), "access_policy": policy,
                "access_policy_review": {"policy_sha256": access_policy_sha256(policy), "target_app_id": "test-app",
                                         "build_id": "build-test", "status": "approved", "review_id": "review-test", "reviewed_by": "owner"},
            }}},
        },
    }


@pytest.mark.parametrize("raw_output", [False, True])
def test_repair_save_and_final_assembly_keep_reviewed_read_permissions(raw_output):
    policy = _policy()
    policy["grants"][0]["actions"].extend(["get_tasks", "list_tasks"])
    contract = _contract()
    context = _reviewed_context(policy, contract=contract)
    context.update(app_build_plan=_plan(), current_build_task={"owned_paths": [MANIFEST]})
    candidate = _output()
    if raw_output:
        candidate = {"code_files": [{"filename": path, "content": content}
                                    for path, content in extract_code_file_map_from_payload(candidate).items()]}
    bridge = ContextVariablesBridge(context)
    saved = save_generated_code(StructuredOutputOverlay(bridge, candidate))
    assert MANIFEST in saved["saved_files"]
    files = extract_code_file_map_from_payload({"code_files": bridge.snapshot()["code_files"]})
    for stage in (files, {entry["filename"]: entry["content"] for entry in _merge_code_files(
        [{"code_files": [{"filename": path, "content": content} for path, content in files.items()],
          "app_json": {"authRequired": True}}],
        app_build_plan=_plan(), data_contract=contract, design_surface_map=_surface(), context_variables=context,
    )}):
        actions = {action["id"]: action for action in yaml.safe_load(stage[MANIFEST])["actions"]}
        assert actions["get_tasks"]["permissions"] == ["tasks.get_tasks"]
        assert actions["list_tasks"]["permissions"] == ["tasks.list_tasks"]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_output", [False, True])
async def test_task_admission_preserves_approved_read_grants_from_current_artifact(monkeypatch, raw_output):
    policy = _policy()
    policy["grants"][0]["actions"].extend(["get_tasks", "list_tasks"])
    context = _reviewed_context(policy, contract=_contract())
    context["app_build_plan"] = _plan()
    task = {"task_id": "contract", "task_type": "module_contract", "capability_pack_id": MODULE,
            "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare the module.",
            "owned_paths": [MANIFEST], "depends_on": [], "context_variables": {}}
    context["app_build_plan"]["build_tasks"] = [task]

    async def run(_runner, _request):
        output = _output()
        if raw_output:
            output = {"code_files": [{"filename": path, "content": content}
                                     for path, content in extract_code_file_map_from_payload(output).items()]}
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=output)

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app/workflows",
    )
    accepted = await task_batches._run_one_task(
        workflow_name="AppGenerator", batch=next(b for b in config.batches if b.id == "app_build_tasks"),
        task=task, all_task_items=[task], base_context=context, completed_task_outputs={}, current_batch_outputs={},
        agents={"ConfigMiddlewareAgent": object()}, chat_id="reads", app_id="test-app", user_id="owner",
        semaphore=asyncio.Semaphore(1), fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
    )
    files = extract_code_file_map_from_payload(accepted)
    actions = {action["id"]: action for action in yaml.safe_load(files[MANIFEST])["actions"]}
    assert actions["get_tasks"]["permissions"] == ["tasks.get_tasks"]
    assert actions["list_tasks"]["permissions"] == ["tasks.list_tasks"]


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
