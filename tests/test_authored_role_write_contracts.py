"""Approved roles protect authored request contracts without claiming their implementation."""
from __future__ import annotations

import importlib
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_file_contracts_body,
)
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.design_access_policy import access_policy_sha256
from mozaiksai.core.workflow.generator_support.module_role_grants import compiled_role_grants
from mozaiksai.core.workflow.generator_support.module_write_actions import (
    close_module_actions,
    materialize_module_actions,
    materialize_module_write_implementations,
    module_write_implementation_ownership,
)
from tests.test_appgenerator_module_write_actions import _contract, _field

MODULE = "borrowing"
MANIFEST = f"modules/{MODULE}/module.yaml"
BACKEND = f"modules/{MODULE}/backend"
WRITES = ("create_borrow_request", "update_borrow_request", "delete_borrow_request")
CLIENT_FIELDS = {"tool_id": "string", "requested_start_date": "string", "requested_duration_days": "integer", "reason": "string"}


def _typed(properties, required):
    # The live contract uses this finite property-list shape, including descriptions.
    return {"type": "object", "description": "Declared request/response", "items_type": None, "properties": [
        {"name": name, "type": kind, "required": name in required, "description": f"Declared {name}",
         "enum_values": [], "items_type": None} for name, kind in properties.items()
    ]}


def _arguments():
    contract = _contract("app_wide", fields=[
        _field("borrow_request_id"), _field("requester_user_id"),
        *[_field(name, kind) for name, kind in CLIENT_FIELDS.items()],
        _field("status", default='"pending"', enum=["pending", "approved", "rejected", "cancelled"]),
        _field("created_at", "datetime"), _field("updated_at", "datetime"),
    ], search_by="borrow_request_id", indexes=[])
    surface = contract["surfaces"][0]
    surface["surface_id"] = MODULE
    collection = surface["collections"][0]
    collection.update(name="borrow_requests", entity="BorrowRequest", ownership={"surface_id": MODULE, "surface_kind": "module"})
    return {
        "app_build_plan": {"capability_packs": [{"capability_pack_id": MODULE,
                            "capability_source": "generated_module", "primary_entities": ["BorrowRequest"]}], "pages": []},
        "data_contract": contract,
        "design_surface_map": {"surfaces": [{"surface_id": MODULE, "surface_kind": "module", "owner": "app",
                                             "owned_mutations": ["approve_borrow_request"]}]},
        "access_policy": {"roles": [{"role_id": role, "label": role, "description": role} for role in ("member", "admin")],
                          "grants": [{"role_id": "member", "collection": "borrow_requests", "actions": list(WRITES)},
                                     {"role_id": "admin", "collection": "borrow_requests", "actions": ["approve_borrow_request"]}]},
    }


def _payload(actions):
    return {"module_contract": {"module_id": MODULE, "module_yaml": {
        "schema_version": "mozaiks.module.v1", "module": {"id": MODULE, "handler": "backend.handler:BorrowingHandler"},
        "actions": actions,
    }}}


def _actions(payload):
    return {a["id"]: a for a in payload["module_contract"]["module_yaml"]["actions"]}


def _candidate(*, authored=True):
    args = _arguments()
    baseline = close_module_actions(_payload([]), app_build_plan=args["app_build_plan"], data_contract=args["data_contract"])
    actions = [a for name, a in _actions(baseline).items() if name not in WRITES]
    output_fields = {"borrow_request_id": "string", "requester_user_id": "string", **CLIENT_FIELDS, "status": "string"}
    if authored:
        requests = [CLIENT_FIELDS, {"borrow_request_id": "string", **{k: v for k, v in CLIENT_FIELDS.items() if k != "tool_id"}},
                    {"borrow_request_id": "string"}]
        for index, name in enumerate(WRITES):
            fields = {"borrow_request_id": "string", "deleted": "boolean"} if index == 2 else output_fields
            actions.append({"id": name, "handler_method": name, "description": "Authored protected write",
                            "api_surface": None, "permissions": None, "emits": [], "ask_context_safe": False,
                            "input_schema": _typed(requests[index], set(CLIENT_FIELDS) if index == 0 else {"borrow_request_id"}),
                            "output_schema": _typed(fields, set(fields))})
    actions.append({"id": "approve_borrow_request", "handler_method": "approve_borrow_request", "api_surface": None,
                    "permissions": None, "emits": [], "ask_context_safe": False,
                    "input_schema": _typed({"borrow_request_id": "string"}, {"borrow_request_id"}),
                    "output_schema": _typed(output_fields, set(output_fields))})
    return _payload(actions)


def _reviewed_context(args):
    policy = args["access_policy"]
    return {
        **args, "run_build_binding": {"build_registry_id": "appreg-test", "target_app_id": "test-app",
                                      "build_id": "build-test", "phase": "genesis"},
        "design_docs_artifact": {"app_id": "test-app", "build_family": "design_docs", "build_key": "design_docs",
                                 "source_workflow": "DesignDocs", "lifecycle_status": "current",
                                 "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": {
                                     "data_contract": args["data_contract"], "surface_map": args["design_surface_map"],
                                     "access_policy": policy, "access_policy_review": {
                                         "policy_sha256": access_policy_sha256(policy), "target_app_id": "test-app",
                                         "build_id": "build-test", "status": "approved", "review_id": "review-test", "reviewed_by": "owner",
                                     },
                                 }}}},
    }


def test_typed_authored_requests_keep_permissions_and_implementation_through_assembly():
    args, candidate = _arguments(), _candidate()
    before = deepcopy(candidate)
    closed = close_module_actions(candidate, **args)
    assert candidate == before
    actions = _actions(closed)
    for action_id in WRITES:
        assert actions[action_id]["input_schema"] == _actions(candidate)[action_id]["input_schema"]
        assert actions[action_id]["output_schema"] == _actions(candidate)[action_id]["output_schema"]
        assert actions[action_id]["permissions"] == [f"borrow_requests.{action_id}"]
    assert close_module_actions(closed, **args) == closed
    files = extract_code_file_map_from_payload(closed)
    authored_sources = {f"{BACKEND}/{name}.py": source for name, source in _sources().items()}
    files.update(authored_sources)
    assert materialize_module_write_implementations(files, **args) == {}
    assert materialize_module_actions(files, **args) == {}
    manifest = yaml.safe_load(files[MANIFEST])
    assert module_write_implementation_ownership(MODULE, manifest, **args) == {"code_owned": [], "authored": list(WRITES)}
    inputs = {a["id"]: a["input_schema"] for a in manifest["actions"]}
    assert set(inputs[WRITES[0]]["properties"]) == set(CLIENT_FIELDS)
    assert inputs[WRITES[0]]["additionalProperties"] is False
    assert set(inputs[WRITES[1]]["required"]) == {"borrow_request_id"}
    files["app.json"] = '{"authRequired": true}'
    assembled = {entry["filename"]: entry["content"] for entry in _merge_code_files(
        [{"code_files": [{"filename": name, "content": content} for name, content in files.items()]}],
        **{key: value for key, value in args.items() if key != "access_policy"}, context_variables=_reviewed_context(args),
    )}
    assert {p: assembled[p] for p in authored_sources} == authored_sources
    final = yaml.safe_load(assembled[MANIFEST])
    assert {a["id"]: a["input_schema"] for a in final["actions"]} == inputs
    auth = yaml.safe_load(assembled["config/auth.yaml"])
    assert auth["authorization"]["roles"] == compiled_role_grants(args["access_policy"])
    assert not any(scope.startswith("borrow_requests.") for scope in auth["frontend"]["default_scopes"])


def test_omitted_and_canonical_equivalent_role_writes_stay_code_owned():
    args = _arguments()
    closed = close_module_actions(_candidate(authored=False), **args)
    for iteration in range(3):
        files = extract_code_file_map_from_payload(closed)
        assert module_write_implementation_ownership(MODULE, yaml.safe_load(files[MANIFEST]), **args) == {
            "code_owned": list(WRITES), "authored": [],
        }
        rendered = materialize_module_write_implementations(files, **args)
        for filename in ("handler.py", "service.py", "repo.py"):
            assert all(f"def {name}(" in rendered[f"{BACKEND}/{filename}"] for name in WRITES)
        assert materialize_module_actions(files, **args) == {}
        candidate = _payload(yaml.safe_load(files[MANIFEST])["actions"])
        if iteration == 0:
            for name in WRITES:
                schema = _actions(candidate)[name]["input_schema"]
                typed = _typed({key: field["type"] for key, field in schema["properties"].items()}, set(schema.get("required", [])))
                for prop in typed["properties"]:
                    prop["enum_values"] = schema["properties"][prop["name"]].get("enum", [])
                _actions(candidate)[name]["input_schema"] = typed
        closed = close_module_actions(candidate, **args)


def test_authored_and_code_owned_collections_share_one_module_without_changing_ownership():
    args, candidate = _arguments(), _candidate()
    collection = _contract("app_wide")["surfaces"][0]["collections"][0]
    collection["ownership"]["surface_id"] = MODULE
    args["data_contract"]["surfaces"][0]["collections"].append(collection)
    args["app_build_plan"]["capability_packs"][0]["primary_entities"].append("Task")
    task_writes = [f"{operation}_task" for operation in ("create", "update", "delete")]
    args["access_policy"]["grants"].append({"role_id": "admin", "collection": "tasks", "actions": task_writes})
    baseline = close_module_actions(_payload([]), app_build_plan=args["app_build_plan"], data_contract=args["data_contract"])
    candidate["module_contract"]["module_yaml"]["actions"].extend(
        action for name, action in _actions(baseline).items() if name in {"get_tasks", "list_tasks"}
    )
    closed = close_module_actions(candidate, **args)
    files = extract_code_file_map_from_payload(closed)
    files.update({f"{BACKEND}/{name}.py": source for name, source in _sources().items()})
    assert module_write_implementation_ownership(MODULE, yaml.safe_load(files[MANIFEST]), **args) == {
        "code_owned": task_writes, "authored": list(WRITES),
    }
    rendered = materialize_module_write_implementations(files, **args)
    assert "async def create_task(" in rendered[f"{BACKEND}/service.py"]
    assert _sources()["service"].strip() in rendered[f"{BACKEND}/service.py"]
    assert close_module_actions(closed, **args) == closed


def test_authored_event_declarations_and_exact_role_grants_survive_closure():
    args, candidate = _arguments(), _candidate()
    event = "domain.borrowing.borrow_request_created"
    action = _actions(candidate)[WRITES[0]]
    action["emits"] = [event]
    action["permissions"] = ["borrow_requests.approve_borrow_request", "made_up_admin"]
    candidate["module_contract"]["events_yaml"] = {"schema_version": "mozaiks.events.v1", "events": [{
        "type": event, "version": 1, "producer": MODULE, "description": "Request created",
        "payload_schema": _typed({"borrow_request_id": "string"}, {"borrow_request_id"}),
    }]}
    closed = close_module_actions(candidate, **args)
    assert _actions(closed)[WRITES[0]]["permissions"] == ["borrow_requests.create_borrow_request"]
    assert _actions(closed)[WRITES[0]]["emits"] == [event]
    assert closed["module_contract"]["events_yaml"] == candidate["module_contract"]["events_yaml"]
    assert close_module_actions(closed, **args) == closed
    with pytest.raises(ValueError, match="not constructed open"):
        close_module_actions(closed, **{**args, "access_policy": {"roles": [], "grants": []}})


@pytest.mark.parametrize("surface", ["public", "public_readonly"])
@pytest.mark.parametrize("authored", [False, True])
def test_role_permissions_never_admit_anonymous_canonical_writes(surface, authored):
    candidate = _candidate(authored=authored)
    if not authored:
        candidate = close_module_actions(candidate, **_arguments())
    _actions(candidate)[WRITES[0]]["api_surface"] = surface
    with pytest.raises(ValueError, match="anonymous canonical writes"):
        close_module_actions(candidate, **_arguments())


@pytest.mark.parametrize("attack", ["missing_action", "missing_output", "unknown_field", "changed_type", "weaken_required",
                                    "update_missing_id", "delete_missing_id", "open_object", "changed_enum"])
def test_authored_role_contracts_reject_incomplete_or_widened_requests(attack):
    candidate = _candidate()
    actions = _actions(candidate)
    create = actions[WRITES[0]]["input_schema"]
    if attack == "missing_action":
        candidate["module_contract"]["module_yaml"]["actions"].remove(actions[WRITES[2]])
    elif attack == "missing_output":
        actions[WRITES[2]].pop("output_schema")
    elif attack == "unknown_field":
        create["properties"].append(_typed({"foreign": "string"}, set())["properties"][0])
    elif attack == "changed_type":
        create["properties"][0]["type"] = "integer"
    elif attack == "weaken_required":
        create["properties"][0]["required"] = False
    elif attack.endswith("missing_id"):
        actions[WRITES[1 if attack.startswith("update") else 2]]["input_schema"]["properties"].pop(0)
    elif attack == "changed_enum":
        create["properties"].append({**_typed({"status": "string"}, set())["properties"][0], "enum_values": ["elevated"]})
    else:
        actions[WRITES[0]]["input_schema"] = {"type": "object", "properties": {}, "additionalProperties": True}
    with pytest.raises(ValueError):
        close_module_actions(candidate, **_arguments())


@pytest.mark.parametrize("stale", [False, True])
def test_service_prompt_uses_admitted_dependencies_before_stale_module_context(stale):
    args = _arguments()
    closed = close_module_actions(_candidate(), **args)
    context = {**_reviewed_context(args), "current_build_task": {"capability_pack_id": MODULE, "owned_paths": [f"{BACKEND}/service.py"]},
               "dependency_task_outputs": {"module_contract": closed}}
    if stale:
        context["module_contract"] = close_module_actions(_candidate(authored=False), **args)["module_contract"]
    body = _build_file_contracts_body(SimpleNamespace(name="ServiceAgent", context_variables=context), {})
    assert "Authored protected write actions" in body
    assert all(f"  - {name}" in body for name in WRITES)
    assert "Code-owned write methods" not in body
    assert "trusted server context" in body


def _sources():
    handler = "from . import service\n\nclass BorrowingHandler:\n" + "".join(
        f"    async def {name}(self, ctx, **params):\n        return await service.{name}(ctx, **params)\n"
        for name in (*WRITES, "approve_borrow_request")
    )
    service = '''from . import repo
from mozaiksai.core.runtime import ModuleRecordNotFoundError

async def create_borrow_request(ctx, **params):
    record = {**params, "borrow_request_id": "request-1", "requester_user_id": ctx.user_id, "status": "pending",
              "created_at": "2027-01-01T00:00:00Z", "updated_at": "2027-01-01T00:00:00Z"}
    await repo.insert_request(ctx, record)
    return record

async def update_borrow_request(ctx, borrow_request_id, **changes):
    record = await repo.load_request(ctx, borrow_request_id)
    if not record or record["requester_user_id"] != ctx.user_id or record["status"] != "pending":
        raise ModuleRecordNotFoundError("Request is not editable")
    await repo.update_request(ctx, borrow_request_id, changes)
    return {**record, **changes}

async def delete_borrow_request(ctx, borrow_request_id):
    record = await repo.load_request(ctx, borrow_request_id)
    if not record or record["requester_user_id"] != ctx.user_id or record["status"] != "pending":
        raise ModuleRecordNotFoundError("Request is not editable")
    await repo.delete_request(ctx, borrow_request_id)
    return {"borrow_request_id": borrow_request_id, "deleted": True}

async def approve_borrow_request(ctx, borrow_request_id):
    record = await repo.load_request(ctx, borrow_request_id)
    if not record or record["status"] != "pending":
        raise ModuleRecordNotFoundError("Request is not pending")
    await repo.update_request(ctx, borrow_request_id, {"status": "approved"})
    return {**record, "status": "approved"}
'''
    repo = '''def collection(ctx):
    return ctx.persistence.collection("borrowing", "borrow_requests")

async def insert_request(ctx, record):
    return await collection(ctx).insert_one(record)

async def load_request(ctx, borrow_request_id):
    return await collection(ctx).find_one({"borrow_request_id": borrow_request_id})

async def update_request(ctx, borrow_request_id, changes):
    return await collection(ctx).update_one({"borrow_request_id": borrow_request_id}, {"$set": changes})

async def delete_request(ctx, borrow_request_id):
    return await collection(ctx).delete_one({"borrow_request_id": borrow_request_id})
'''
    return {"handler": handler, "service": service, "repo": repo}


@pytest.mark.asyncio
async def test_actual_executor_keeps_identity_and_lifecycle_behind_authored_closed_requests(tmp_path, monkeypatch):
    from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority
    from mozaiksai.core.runtime.composition.module_context import ModuleContext
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
    from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
    from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceCollection
    from tests.test_appgenerator_module_write_actions import _RawCollection

    args = _arguments()
    files = extract_code_file_map_from_payload(close_module_actions(_candidate(), **args))
    files.update({f"{BACKEND}/{name}.py": source for name, source in _sources().items()})
    files.update(materialize_module_write_implementations(files, **args))
    package = tmp_path / "authored_borrowing"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name in ("handler", "service", "repo"):
        (package / f"{name}.py").write_text(files[f"{BACKEND}/{name}.py"])
    monkeypatch.syspath_prepend(str(tmp_path))
    handler = importlib.import_module("authored_borrowing.handler").BorrowingHandler()
    manifest = yaml.safe_load(files[MANIFEST])
    actions = [a for a in manifest["actions"] if a["id"] in (*WRITES, "approve_borrow_request")]
    executor = ModuleExecutor()
    monkeypatch.setattr(executor, "_emit_dispatch_audit", AsyncMock())
    executor.register(MODULE, handler, action_method_map={a["id"]: a["handler_method"] for a in actions},
                      action_permissions={a["id"]: a["permissions"] for a in actions},
                      action_schemas={a["id"]: {"input": a["input_schema"], "output": a["output_schema"]} for a in actions})
    raw = _RawCollection()
    grants = {row["id"]: tuple(row["permissions"]) for row in compiled_role_grants(args["access_policy"])}

    async def invoke(action, params, *, user="member-a", role="member"):
        authority = ModuleDispatchAuthority(kind="authenticated_user", permission_mode="enforce", reason="test request",
                                            actor_id=user, permissions=grants.get(role, ()))
        principal = PersistencePrincipal(user_id=user, workspace_id="library")
        collection = MongoPersistenceCollection(collection=raw, app_id="test-app", user_id=user,
                                                 ownership=None, principal=principal)
        context = ModuleContext(app_id="test-app", user_id=user, workspace_id="library")
        context.persistence = SimpleNamespace(collection=lambda module, name: collection)
        return await executor.execute(ModuleRequest(module=MODULE, action=action, params=params, app_id="test-app",
                                                     user_id=user, authority=authority), context=context)

    payload = {"tool_id": "tool-1", "requested_start_date": "2027-01-01", "requested_duration_days": 2, "reason": "Repair"}
    assert (await invoke(WRITES[0], payload, role="roleless")).error_code == "PERMISSION_DENIED"
    assert raw.rows == []
    for field, value in (("requester_user_id", "member-b"), ("status", "approved")):
        assert (await invoke(WRITES[0], {**payload, field: value})).error_code == "INVALID_PARAMS"
        assert raw.rows == []
    created = await invoke(WRITES[0], payload)
    assert created.success, created
    assert raw.rows[0]["requester_user_id"] == "member-a" and raw.rows[0]["status"] == "pending"
    identity = {"borrow_request_id": "request-1"}
    before = deepcopy(raw.rows)
    assert (await invoke(WRITES[1], {**identity, "reason": "stolen"}, user="member-b")).error_code == "RECORD_NOT_FOUND"
    assert (await invoke(WRITES[1], {**identity, "status": "approved"})).error_code == "INVALID_PARAMS"
    assert (await invoke("approve_borrow_request", identity)).error_code == "PERMISSION_DENIED"
    assert raw.rows == before
    assert (await invoke(WRITES[1], {**identity, "reason": "Updated"})).success
    assert (await invoke("approve_borrow_request", identity, user="admin-a", role="admin")).success
    assert raw.rows[0]["status"] == "approved"
    before = deepcopy(raw.rows)
    assert (await invoke(WRITES[1], {**identity, "reason": "post approval"})).error_code == "RECORD_NOT_FOUND"
    assert (await invoke(WRITES[2], identity)).error_code == "RECORD_NOT_FOUND"
    assert raw.rows == before
