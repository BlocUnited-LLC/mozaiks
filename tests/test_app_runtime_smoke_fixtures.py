"""Trusted observer inputs prepare real state without defining acceptance outcomes."""
from __future__ import annotations

import json
import os

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke
from factory_app.workflows.AppGenerator.tools.app_validation import _write_files_to_dir
from tests.test_app_runtime_role_smoke import _role_bundle
from tests.test_app_runtime_smoke import MODULE_YAML, _by_check, _contained_smoke, _smoke
from tests.test_app_runtime_smoke import mongo as _mongo

mongo = _mongo
FIXTURE_PATH = "modules/task_management/contracts/fixtures.yaml"


def literal(field, value):
    return {"field": field, "source": "literal", "value_json": json.dumps(value)}


def record(field):
    return {"field": field, "source": "record", "record_field": field}


def tomorrow(field):
    return {"field": field, "source": "relative_date", "offset_days": 1}


def fixture_bundle(*, broken=False, internal_reads=False):
    files = _role_bundle()
    files.pop("config/subscriptions.yaml")
    module = yaml.safe_load(files[MODULE_YAML])
    for action in module["actions"]:
        action.pop("entitlement_gate", None)
        if internal_reads and action["id"] in {"get_tasks", "list_tasks"}:
            action["api_surface"] = "internal"
    permission = "tasks.approve_task"
    module["permissions"].append({"id": permission, "description": "Review a prepared task."})
    module["actions"].append({
        "id": "approve_task", "handler_method": "approve_task", "description": "Review a pending task.",
        "permissions": [permission], "api_surface": None, "emits": ["domain.task.updated"],
        "input_schema": {"type": "object", "additionalProperties": False,
                         "properties": {name: {"type": "string"}
                                        for name in ("task_id", "expected_owner", "review_date")},
                         "required": ["task_id", "expected_owner", "review_date"]},
        "output_schema": module["actions"][0]["output_schema"],
    })
    files[MODULE_YAML] = yaml.safe_dump(module)
    auth = yaml.safe_load(files["config/auth.yaml"])
    auth["authorization"]["roles"].append({"id": "reviewer", "permissions": [permission]})
    files["config/auth.yaml"] = yaml.safe_dump(auth)
    files["modules/task_management/backend/handler.py"] += (
        "\n    async def approve_task(self, ctx, task_id, expected_owner, review_date):\n"
        "        return await service.approve_task(ctx, task_id, expected_owner, review_date)\n"
    )
    files["modules/task_management/backend/service.py"] += '''

async def approve_task(ctx, task_id, expected_owner, review_date):
    from datetime import datetime, timezone
    record = await repo.load_task(ctx, task_id)
    if (record['status'] != 'pending' or record['user_id'] != ctx.user_id
            or expected_owner != ctx.user_id or record['description'] != review_date
            or review_date <= datetime.now(timezone.utc).date().isoformat()):
        raise ModuleInputValidationError('Pending owned task with a future review date required')
    updated = await repo.update_task(ctx, task_id, {'status': 'completed'})
    item = schemas.serialize_task(updated)
    await ctx.emit('domain.task.updated', item)
    return {'item': item}
'''.replace("    updated =", "    raise ModuleInputValidationError('Review is broken')\n    updated =" if broken else "    updated =")
    create = [literal("title", "Prepared task"), literal("status", "pending"), tomorrow("description")]
    fixtures = [
        {"action": "create_task", "roles": ["coordinator"], "setup": [], "params": create},
        {"action": "update_task", "roles": ["coordinator"], "setup": [],
         "params": [record("task_id"), literal("title", "Updated fixture task")]},
        {"action": "delete_task", "roles": ["coordinator"], "setup": [], "params": [record("task_id")]},
        {"action": "approve_task", "roles": ["reviewer"],
         "setup": [{"id": "pending", "module": "task_management", "action": "create_task",
                    "roles": ["coordinator"], "params": create}],
         "params": [{"field": "task_id", "source": "response", "step_id": "pending", "response_path": ["item", "task_id"]},
                    {"field": "expected_owner", "source": "actor_subject"}, tomorrow("review_date")]},
    ]
    files[FIXTURE_PATH] = yaml.safe_dump({"schema_version": "mozaiks.module_smoke_fixtures.v1", "fixtures": fixtures})
    return files


def authored_read_fixture_bundle(lookup_field="task_id"):
    files = fixture_bundle()
    contract = json.loads(files["data/contract.json"])
    surface = next(item for item in contract["surfaces"] if item["surface_id"] == "task_management")
    surface["collections"][0]["search_by"] = lookup_field
    files["data/contract.json"] = json.dumps(contract)
    module = yaml.safe_load(files[MODULE_YAML])
    for action in module["actions"]:
        if action["id"] == "get_tasks":
            action["input_schema"] = {
                "type": "object", "additionalProperties": False,
                "properties": {"lookup": {"type": "string"}}, "required": ["lookup"],
            }
        elif action["id"] == "list_tasks":
            action["input_schema"]["properties"]["proof_filter"] = {"type": "string", "enum": ["all"]}
            action["input_schema"].setdefault("required", []).append("proof_filter")
    files[MODULE_YAML] = yaml.safe_dump(module)
    handler = "modules/task_management/backend/handler.py"
    files[handler] = files[handler].replace("get_tasks(self, ctx, *, id)", "get_tasks(self, ctx, *, lookup)")
    files[handler] = files[handler].replace("service.get_tasks(ctx, id=id)", "service.get_tasks(ctx, id=lookup)")
    for filename in (handler, "modules/task_management/backend/service.py"):
        files[filename] = files[filename].replace(
            "list_tasks(self, ctx, *, page=", "list_tasks(self, ctx, *, proof_filter, page=",
        ).replace("list_tasks(ctx, *, page=", "list_tasks(ctx, *, proof_filter, page=")
    files[handler] = files[handler].replace(
        "service.list_tasks(ctx, page=", "service.list_tasks(ctx, proof_filter=proof_filter, page=",
    )
    repo = "modules/task_management/backend/repo.py"
    files[repo] = files[repo].replace("filters = {'task_id': id}", f"filters = {{{lookup_field!r}: id}}", 1)
    document = yaml.safe_load(files[FIXTURE_PATH])
    document["fixtures"].extend([
        {"action": "get_tasks", "roles": [], "setup": [],
         "params": [{"field": "lookup", "source": "record", "record_field": lookup_field}]},
        {"action": "list_tasks", "roles": [], "setup": [], "params": [literal("proof_filter", "all")]},
    ])
    files[FIXTURE_PATH] = yaml.safe_dump(document)
    return files


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_field", ["task_id", "title"])
async def test_read_fixtures_preserve_authored_inputs_actual_row_and_isolation(mongo, lookup_field):
    result = await _smoke(authored_read_fixture_bundle(lookup_field), mongo.uri)
    assert result["passed"] is True, result["failed_tests"]
    checks = _by_check(result)
    for name in ("a_get", "get_missing", "b_get_isolated", "a_list", "b_list_isolated"):
        assert checks[f"crud.task_management.tasks.{name}"]["status"] == "passed"


def typed_authored_read_fixture_bundle(kind, initial, changed, *, enum=None):
    files = authored_read_fixture_bundle("title")
    contract = json.loads(files["data/contract.json"])
    collection = next(surface for surface in contract["surfaces"]
                      if surface["surface_id"] == "task_management")["collections"][0]
    title = next(field for field in collection["fields"] if field["name"] == "title")
    title.update(type=kind, enum=enum)
    files["data/contract.json"] = json.dumps(contract)
    module = yaml.safe_load(files[MODULE_YAML])

    def replace_title_schema(value):
        if isinstance(value, dict):
            properties = value.get("properties", {})
            if "title" in properties:
                properties["title"] = {"type": kind, **({"enum": enum} if enum else {})}
            for child in value.values():
                replace_title_schema(child)
        elif isinstance(value, list):
            for child in value:
                replace_title_schema(child)

    replace_title_schema(module)
    get = next(action for action in module["actions"] if action["id"] == "get_tasks")
    get["input_schema"]["properties"]["lookup"] = {"type": kind, **({"enum": enum} if enum else {})}
    permission = "tasks.typed_read"
    get["permissions"] = [permission]
    module["permissions"].append({"id": permission, "description": "Read one task by its typed lookup."})
    files[MODULE_YAML] = yaml.safe_dump(module)
    auth = yaml.safe_load(files["config/auth.yaml"])
    auth["authorization"]["roles"].append({"id": "lookup_reader", "permissions": [permission]})
    files["config/auth.yaml"] = yaml.safe_dump(auth)
    events_path = "modules/task_management/contracts/events.yaml"
    events = yaml.safe_load(files[events_path])
    replace_title_schema(events)
    files[events_path] = yaml.safe_dump(events)
    fixtures = yaml.safe_load(files[FIXTURE_PATH])
    for fixture in fixtures["fixtures"]:
        if fixture["action"] == "get_tasks":
            fixture["roles"] = ["lookup_reader"]
        for params in [fixture["params"], *[step["params"] for step in fixture["setup"]]]:
            for binding in params:
                if binding["field"] == "title":
                    binding["value_json"] = json.dumps(changed if fixture["action"] == "update_task" else initial)
        if fixture["action"] == "update_task" and enum:
            fixture["params"] = [record("task_id"), literal("status", "completed")]
    files[FIXTURE_PATH] = yaml.safe_dump(fixtures)
    return files


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,initial,changed", [("integer", 0, 3), ("number", 0.0, 3.5),
                                                  ("boolean", False, True)])
async def test_authored_get_keeps_typed_zero_and_false_lookup_values(mongo, kind, initial, changed):
    result = await _smoke(typed_authored_read_fixture_bundle(kind, initial, changed), mongo.uri)
    assert result["passed"] is True, result["failed_tests"]
    checks = _by_check(result)
    for name in ("a_get", "get_missing", "b_get_isolated"):
        assert checks[f"crud.task_management.tasks.{name}"]["status"] == "passed"
    expected_method = "GET" if kind == "integer" else "POST"
    for check in ("holder_allowed", "non_holder_denied"):
        proof = checks[f"role.task_management.get_tasks.{check}"]
        assert proof["status"] == "passed" and proof["details"]["method"] == expected_method
    assert checks["role.task_management.get_tasks.non_holder_denied"]["details"]["records_unchanged"] is True


@pytest.mark.asyncio
async def test_missing_lookup_proof_fails_when_all_schema_valid_values_exist(mongo):
    files = typed_authored_read_fixture_bundle("boolean", False, False, enum=[False])
    result = await _smoke(files, mongo.uri)
    assert result["passed"] is False
    checks = _by_check(result)
    missing = checks["crud.task_management.tasks.get_missing"]
    assert missing["status"] == "failed"
    assert "missing-record proof is incomplete: no schema-valid absent lookup value" in missing["message"]
    assert checks["crud.task_management.tasks.a_get"]["status"] == "passed"


@pytest.mark.asyncio
async def test_authored_get_fixture_does_not_hide_leaked_record(mongo):
    files = authored_read_fixture_bundle()
    repo = "modules/task_management/backend/repo.py"
    files[repo] = files[repo].replace(
        "record = await collection.find_one(filters)",
        "record = await collection._collection.find_one(filters)",
    )
    result = await _smoke(files, mongo.uri)
    assert result["passed"] is False
    check = _by_check(result)["crud.task_management.tasks.b_get_isolated"]
    assert check["status"] == "failed" and "returned user A's record" in check["message"]


def test_observer_copies_only_declarative_fixture_input_and_loads_actual_bytes(tmp_path):
    root, probe = tmp_path / "app", tmp_path / "probe"
    _write_files_to_dir(root, fixture_bundle())
    smoke._copy_probe_plan(root, probe)
    assert (probe / FIXTURE_PATH).read_bytes() == (root / FIXTURE_PATH).read_bytes()
    assert not list(probe.rglob("*.py"))
    run = smoke._SmokeRun(probe, None, "unused", lambda *_args, **_kwargs: None)
    smoke._probe_contracts(run)
    assert set(run.fixtures) == {("task_management", action) for action in
                                 ("create_task", "update_task", "delete_task", "approve_task")}


@pytest.mark.asyncio
@pytest.mark.parametrize("internal_reads", [False, True])
async def test_real_observer_prepares_state_and_proves_roles_without_widening_them(mongo, internal_reads):
    result = await _smoke(fixture_bundle(internal_reads=internal_reads), mongo.uri)
    assert result["passed"] is True, result["failed_tests"]
    checks = _by_check(result)
    for action in ("create_task", "update_task", "delete_task", "approve_task"):
        assert checks[f"role.task_management.{action}.holder_allowed"]["status"] == "passed"
        denial = checks[f"role.task_management.{action}.non_holder_denied"]
        assert denial["status"] == "passed" and denial["details"]["records_unchanged"] is True
    for name in ("a_create", "a_update", "a_delete", "b_update_denied", "b_delete_denied"):
        assert checks[f"crud.task_management.tasks.{name}"]["status"] == "passed"
    if internal_reads:
        for name in ("get", "list"):
            assert checks[f"crud.task_management.tasks.{name}"]["status"] == "not_run"
    else:
        for name in ("a_get", "a_list", "b_get_isolated", "b_list_isolated"):
            assert checks[f"crud.task_management.tasks.{name}"]["status"] == "passed"
    holders = [principal for principal in result["principals"].values() if principal["roles"]]
    assert {tuple(principal["roles"]) for principal in holders} == {("coordinator",), ("reviewer",)}


@pytest.mark.asyncio
async def test_prerequisite_success_cannot_mask_broken_target(mongo):
    result = await _smoke(fixture_bundle(broken=True), mongo.uri)
    checks = _by_check(result)
    assert result["passed"] is False
    assert checks["role.task_management.create_task.holder_allowed"]["status"] == "passed"
    assert checks["role.task_management.approve_task.non_holder_denied"]["status"] == "passed"
    assert checks["role.task_management.approve_task.holder_allowed"]["status"] == "failed"


@pytest.mark.asyncio
async def test_invalid_reference_blocks_observer_before_any_action(tmp_path):
    files = fixture_bundle()
    fixture = yaml.safe_load(files[FIXTURE_PATH])
    fixture["fixtures"][-1]["params"][0]["step_id"] = "missing"
    files[FIXTURE_PATH] = yaml.safe_dump(fixture)
    root = tmp_path / "app"
    _write_files_to_dir(root, files)
    run = smoke._SmokeRun(root, None, "unused", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="earlier setup"):
        smoke._probe_contracts(run)
    assert run.http is None and not run.principals and not run.role_successes


@pytest.mark.asyncio
async def test_fixture_cannot_replace_observer_record_or_union_roles(tmp_path):
    files = fixture_bundle()
    root = tmp_path / "app"
    _write_files_to_dir(root, files)
    run = smoke._SmokeRun(root, None, "unused", lambda *_args, **_kwargs: None)
    smoke._probe_contracts(run)
    module = next(item for item in run.load.modules if item.name == "task_management")
    principal = run.principal("A")
    prepared = await smoke._prepare_action_inputs(run, module, "update_task", principal, {},
                                                 record={"task_id": "actual"}, identity={"task_id": "other"})
    assert prepared is None and run.outcomes[-1].passed is False
    actor = smoke._fixture_principal(run, principal, ["reviewer"], "explicit")
    assert run._with_action_roles(actor, ["tasks.create_task"]) is actor
    assert actor.roles == ("reviewer",) and actor.user_id == principal.user_id


@pytest.mark.asyncio
async def test_missing_or_wrong_response_does_not_fall_back_to_synthetic_id(tmp_path):
    root = tmp_path / "app"
    _write_files_to_dir(root, fixture_bundle())
    run = smoke._SmokeRun(root, None, "unused", lambda *_args, **_kwargs: None)
    smoke._probe_contracts(run)
    module = next(item for item in run.load.modules if item.name == "task_management")
    calls = []

    async def request(method, owner, action, principal, params):
        calls.append((owner, action, principal.roles, params))
        return 200, {"item": {"task_id": 7}}, "200"

    run._request = request
    prepared = await smoke._prepare_action_inputs(run, module, "approve_task", run.principal("A"), {})
    assert prepared is None
    assert len(calls) == 1 and calls[0][2] == ("coordinator",)
    assert not run.role_successes and not run.role_denials_checked
    assert "schema" in run.outcomes[-1].message


@pytest.mark.asyncio
async def test_cross_module_setup_dispatches_sequentially_with_observed_response_values(tmp_path):
    files = fixture_bundle()
    preparation = yaml.safe_load(files[MODULE_YAML])
    preparation["module"]["id"] = "review_preparation"
    preparation["capabilities"] = []
    permission = "preparations.prepare_review"
    preparation["permissions"] = [{"id": permission, "description": "Prepare a review input."}]
    preparation["actions"] = [{
        "id": "prepare_review", "handler_method": "prepare_review", "permissions": [permission],
        "description": "Prepare inputs for a review.",
        "input_schema": {"type": "object", "additionalProperties": False,
                         "properties": {"title": {"type": "string"}}, "required": ["title"]},
        "output_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "review_date": {"type": "string"}},
            "required": ["title", "review_date"]},
    }]
    files["modules/review_preparation/module.yaml"] = yaml.safe_dump(preparation)
    auth = yaml.safe_load(files["config/auth.yaml"])
    auth["authorization"]["roles"].append({"id": "preparer", "permissions": [permission]})
    files["config/auth.yaml"] = yaml.safe_dump(auth)
    document = yaml.safe_load(files[FIXTURE_PATH])
    target = document["fixtures"][-1]
    target["setup"].insert(0, {
        "id": "review", "module": "review_preparation", "action": "prepare_review", "roles": ["preparer"],
        "params": [literal("title", "Requested preparation")],
    })
    target["setup"][1]["params"] = [
        {"field": "title", "source": "response", "step_id": "review", "response_path": ["title"]},
        literal("status", "pending"),
        {"field": "description", "source": "response", "step_id": "review", "response_path": ["review_date"]},
    ]
    target["params"][-1] = {
        "field": "review_date", "source": "response", "step_id": "review", "response_path": ["review_date"],
    }
    files[FIXTURE_PATH] = yaml.safe_dump(document)
    root = tmp_path / "app"
    _write_files_to_dir(root, files)
    run = smoke._SmokeRun(root, None, "unused", lambda *_args, **_kwargs: None)
    smoke._probe_contracts(run)
    module = next(item for item in run.load.modules if item.name == "task_management")
    principal = run.principal("A")
    calls = []

    async def request(method, owner, action, actor, params):
        calls.append((method, owner, action, actor.roles, params))
        assert actor.user_id == principal.user_id and actor.exact_roles is True
        if owner == "review_preparation":
            return 201, {"title": f"Observed for {principal.user_id}", "review_date": "2030-02-03"}, "201"
        assert len(calls) == 2 and owner == "task_management" and action == "create_task"
        assert params == {"title": f"Observed for {principal.user_id}", "status": "pending", "description": "2030-02-03"}
        return 201, {"item": {"task_id": f"created-for-{principal.user_id}"}}, "201"

    run._request = request
    prepared = await smoke._prepare_action_inputs(run, module, "approve_task", principal, {})
    assert prepared is not None
    actor, params = prepared
    assert [(owner, action, roles) for _method, owner, action, roles, _params in calls] == [
        ("review_preparation", "prepare_review", ("preparer",)),
        ("task_management", "create_task", ("coordinator",)),
    ]
    assert params == {"task_id": f"created-for-{principal.user_id}", "expected_owner": principal.user_id,
                      "review_date": "2030-02-03"}
    assert actor.roles == ("reviewer",) and actor.exact_roles is True
    assert not run.role_successes and not run.role_denials_checked and not run.outcomes


@pytest.mark.asyncio
async def test_no_op_update_fixture_cannot_satisfy_independent_crud_check(mongo):
    files = fixture_bundle()
    document = yaml.safe_load(files[FIXTURE_PATH])
    update = next(item for item in document["fixtures"] if item["action"] == "update_task")
    update["params"] = [record("task_id"), literal("title", "Prepared task")]
    files[FIXTURE_PATH] = yaml.safe_dump(document)
    result = await _smoke(files, mongo.uri)
    checks = _by_check(result)
    assert result["passed"] is False
    assert checks["crud.task_management.tasks.a_create"]["status"] == "passed"
    rejection = checks["fixture.task_management.update_task"]
    assert rejection["status"] == "failed"
    assert "must change an observable declared record field" in rejection["message"]
    assert checks.get("crud.task_management.tasks.a_update", {}).get("status") != "passed"


@pytest.mark.skipif(not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
                    reason="requires a freshly built local preview image")
@pytest.mark.asyncio
async def test_contained_observer_uses_fixtures_without_candidate_python_in_observer():
    result = await _contained_smoke(fixture_bundle(), image=os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"])
    assert result["passed"] is True, result["failed_tests"]
    assert result["observer_origin"] == "trusted_external_probe_v1"
    assert result["observer_unverified_checks"] == ["event_rejection"]
    checks = _by_check(result)
    assert checks["role.task_management.approve_task.holder_allowed"]["status"] == "passed"
    denial = checks["role.task_management.approve_task.non_holder_denied"]
    assert denial["status"] == "passed" and denial["details"]["records_unchanged"] is True
