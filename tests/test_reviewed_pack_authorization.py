"""Reviewed pack actions compile only their verified dispatch prerequisites."""
from __future__ import annotations

import hashlib
import importlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.design_access_policy import inject_design_access_policy
from factory_app.workflows._shared.pack_selection import pin_pack_choices
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackIntegrityError,
    resolve_managed_capability_templates,
    verify_pack_integrity,
)
from factory_app.workflows.DesignDocs.tools import save_design_doc
from factory_app.workflows.DesignDocs.tools.save_design_doc import (
    _compose_reviewed_pack_data,
    _validate_design_collections,
)
from mozaiksai.core.runtime.composition import (
    ExecutorRegistry,
    ModuleActionDispatchRequest,
    ModuleDispatchAuthority,
    ModuleDispatchScope,
    ModuleExecutor,
    dispatch_module_action,
)
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_authored_module_access_policy,
    resolve_design_access_policy,
)
from mozaiksai.core.workflow.generator_support.module_role_grants import (
    compiled_role_grants,
    validate_access_policy,
    validate_pack_permission_consumers,
    verified_pack_authorization,
)
from tests import test_app_compilation_inputs as compilation
from tests import test_designdocs_monetization_inventory as inventory

persistence = inventory.persistence
saved = compilation.saved

ROOT = Path(__file__).resolve().parents[1]


def _candidate_authorization(context):
    return verified_pack_authorization(context, selection=pin_pack_choices(
        context, [row["id"] for row in context["capability_packs"]],
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("pack_id,module_id,permission,collection,action", [
    ("social", "friends", "friends.manage", "requests", "send_friend_request"),
    ("messaging", "messages", "messages.write", "threads", "send_message"),
])
async def test_pack_review_survives_capture_registration_and_bound_replay(
    pack_id, module_id, permission, collection, action, persistence, saved, monkeypatch, tmp_path,
):
    from factory_app.workflows.AppGenerator.tools import generate_and_download as download
    from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes
    from mozaiksai.core.session.build_context import load_trusted_build_context
    from mozaiksai.core.validation.compilation_inputs import resolve_app_compilation_inputs
    from mozaiksai.core.workflow.context.authority import build_context_authority_policy
    from tests.test_designdocs_selected_pack_context import _prompt_runner

    root = tmp_path / "build_context"
    registry = root / "selection"
    registry.mkdir(parents=True)
    (registry / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "selection", "applies_to_workflows": ["DesignDocs"], "assets": [],
        "values": {"operator_capabilities": [pack_id]},
        "projections": {"context_variables": {"operator_capabilities": {"from": "operator_capabilities"}}},
    }), encoding="utf-8")
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(ROOT / "factory_app"))
    definitions = yaml.safe_load((ROOT / "factory_app/workflows/DesignDocs/context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    selection = load_trusted_build_context(
        build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions), build_context_root=root,
    )
    assert [row["id"] for row in selection["capability_packs"]] == [pack_id]
    authorization = _candidate_authorization(selection)
    actions = [name for name, permissions in authorization.actions[module_id].items() if permission in permissions]
    policy = {"roles": [{"role_id": "member", "label": "Member", "description": "Use reviewed actions."}],
              "grants": [{"role_id": "member", "collection": collection, "actions": actions}]}
    store, context, parent = saved
    design_context = inventory._context(monetized=False)
    design_context.set("run_build_binding", context["run_build_binding"])
    design_context.set("capability_packs", selection["capability_packs"])
    design_context.set("pack_selection", [row.model_dump() for row in authorization.identities])
    design_context.set("operator_contracts", selection.get("operator_contracts", []))
    prompt = await _prompt_runner(design_context)._run_prompt_middleware(SimpleNamespace(variables={}, prompt=[]))
    assert f"{module_id}.{action}" in prompt
    bundle = inventory._bundle(pricing=False)
    bundle["access_policy"] = policy

    async def approve(_tool_id, payload, **kwargs):
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", AsyncMock(side_effect=approve))
    tools = yaml.safe_load((ROOT / "factory_app/workflows/DesignDocs/tools.yaml").read_text(encoding="utf-8"))["tools"]
    entry = next(tool for tool in tools if tool["function"] == "save_design_docs_bundle")
    wrapped = inventory.wrap_tool_outcome(
        save_design_doc.save_design_docs_bundle, inventory.ToolOutcomeSpec.model_validate(entry["outcome"]),
    )
    result = await wrapped(context_variables=inventory.StructuredOutputOverlay(design_context, bundle))
    assert result["outcome"] == "saved", result
    summary = persistence[2].await_args.kwargs["summary_payload"]
    design = store.records["design"]
    design.commit_metadata.metadata["summary_payload"] = summary
    raw = _summary_bytes(summary)
    design.files_manifest[0].sha256 = hashlib.sha256(raw).hexdigest()
    design.files_manifest[0].size_bytes = len(raw)
    design_context.set("design_docs_artifact", design.model_dump(mode="json", by_alias=True))
    templates = resolve_managed_capability_templates(selection["capability_packs"], context_variables=design_context)
    assembled = _merge_code_files(
        [{"code_files": [*templates, {"filename": "app.json", "content": '{"authRequired": true}'}]}],
        data_contract=summary["data_contract"], design_surface_map=summary["surface_map"],
        context_variables=design_context,
    )
    roles = yaml.safe_load(next(row["content"] for row in assembled if row["filename"] == "config/auth.yaml"))["authorization"]["roles"]
    assert roles == [{"id": "member", "permissions": [permission]}]
    context.update(
        app_build_plan={"capability_packs": deepcopy(selection["capability_packs"]), "build_tasks": []},
        capability_packs=deepcopy(selection["capability_packs"]),
        data_contract=summary["data_contract"], design_surface_map=summary["surface_map"],
        design_docs_artifact=design.model_dump(mode="json", by_alias=True),
        app_bundle_acceptance_status="passed",
    )
    artifacts = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts, "resolve_latest_artifact_version_refs", AsyncMock(return_value={"design_docs": "design"}))
    child = await download._register_app_bundle_artifact_version(
        app_id="target", user_id="owner", workflow_name="AppGenerator", chat_id="chat", bundle_name="bundle",
        zip_path=Path(parent.commit_metadata.metadata["artifact_path"]), context_variables=context, artifact_store=store,
    )
    assert child.commit_metadata.metadata["compilation_inputs"]["inputs"]["app_build_plan"]["capability_packs"][0]["id"] == pack_id
    assert child.commit_metadata.metadata["capability_packs"][0]["id"] == pack_id

    async def replay(**overrides):
        return await resolve_app_compilation_inputs(**{
            "app_id": "target", "build_record_id": child.id, "owner_user_id": "owner",
            "build_registry_id": "registry", "artifact_store": store, **overrides,
        })

    assert resolve_design_access_policy((await replay()).context_for("target")) == policy
    rows = deepcopy(child.commit_metadata.metadata["capability_packs"])
    child.commit_metadata.metadata.pop("capability_packs")
    with pytest.raises(ValueError, match="selected pack identities"):
        await replay()
    child.commit_metadata.metadata["capability_packs"] = deepcopy(rows)
    child.commit_metadata.metadata["capability_packs"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="pack sources could not be verified"):
        await replay()
    other_id = "messaging" if pack_id == "social" else "social"
    other_pack = ROOT / "factory_app/build_context" / other_id
    other_identity = verify_pack_integrity(other_pack, other_id)
    child.commit_metadata.metadata["capability_packs"] = [{
        "id": other_id, "pack_source_path": str(other_pack), "capability_source": "generated_module",
        "version": other_identity["version"], "digest": other_identity["digest"],
    }]
    with pytest.raises(ValueError, match="selected pack identities"):
        await replay()
    child.commit_metadata.metadata["capability_packs"] = rows
    child.commit_metadata.metadata["build_id"] = "stale-build"
    with pytest.raises(ValueError):
        await replay()
    child.commit_metadata.metadata["build_id"] = "genesis"
    child.commit_metadata.author_user_id = "foreign"
    with pytest.raises(ValueError, match="outside the owned build"):
        await replay()
    child.commit_metadata.author_user_id = "owner"
    with pytest.raises(ValueError):
        await replay(owner_user_id="foreign")
    with pytest.raises(ValueError):
        await replay(build_registry_id="foreign")
    store.records["design"] = design.model_copy(update={"lifecycle_status": type(design.lifecycle_status).STALE})
    with pytest.raises(ValueError):
        await replay()


@pytest.fixture
def social():
    root = ROOT / "factory_app/build_context/social"
    metadata = verify_pack_integrity(root, "social")
    context = {"capability_packs": [{
        "id": "social", "pack_source_path": str(root), "capability_source": "generated_module",
        "version": metadata["version"], "digest": metadata["digest"],
    }]}
    authorization = _candidate_authorization(context)
    surface_map = {"surfaces": []}
    data = _compose_reviewed_pack_data({"version": "1", "surfaces": [], "shared_collections": []}, surface_map, authorization)
    _validate_design_collections(data, surface_map, pack_authorization=authorization)
    actions = [action for action, permissions in authorization.actions["friends"].items() if "friends.manage" in permissions]
    policy = {
        "roles": [{"role_id": "member", "label": "Member", "description": "Reviewed friend actions."}],
        "grants": [{"role_id": "member", "collection": "requests", "actions": actions}],
    }
    return context, authorization, data, surface_map, policy


@pytest.fixture(scope="module")
def request_pack_authorization():
    context = {}
    selection = pin_pack_choices(context, ["messaging", "social", "support"])
    return verified_pack_authorization(context, selection=selection)


@pytest.fixture
def notes_with_request_packs(request_pack_authorization):
    bundle = inventory._bundle(pricing=False)
    surface = bundle["surface_map"]["surfaces"][0]
    surface["primary_entities"].append("Note")
    group = bundle["data_contract"]["surfaces"][0]
    group["collections"].append({
        "name": "notes", "scope": "app", "entity": "Note", "tenancy": "app_wide", "owner_field": None,
        "ownership": {"surface_id": group["surface_id"], "surface_kind": "module"},
        "fields": [{"name": "id", "type": "string", "required": True}], "indexes": [],
        "search_by": "id", "lifecycle": {"write_mode": "workflow_write", "migration_policy": "additive_only"},
    })
    surface_map = deepcopy(bundle["surface_map"])
    data = _compose_reviewed_pack_data(bundle["data_contract"], surface_map, request_pack_authorization)
    return bundle, {
        "data_contract": data, "design_surface_map": surface_map, "pack_authorization": request_pack_authorization,
    }


def _request_policy():
    return {
        "roles": [
            {"role_id": "reader", "label": "Reader", "description": "Read notes."},
            {"role_id": "member", "label": "Member", "description": "Manage friendships."},
            {"role_id": "moderator", "label": "Moderator", "description": "Update support request status."},
        ],
        "grants": [
            {"role_id": "reader", "collection": "notes", "actions": ["get_notes", "list_notes"]},
            {"role_id": "member", "collection": "friends.requests", "actions": [
                "send_friend_request", "accept_friend_request", "decline_friend_request",
                "withdraw_friend_request", "remove_friend",
            ]},
            {"role_id": "moderator", "collection": "support.requests", "actions": ["update_support_status"]},
        ],
    }


def test_shared_prompt_keeps_authored_permissions_without_a_projected_data_contract(monkeypatch, persistence):
    from tests.test_design_access_policy import _approved_context

    reviewed, summary = _approved_context(monkeypatch, persistence)
    current = reviewed.snapshot()
    current.pop("data_contract", None)
    assert summary["data_contract"] and not summary["access_policy_review"].get("pack_identities")
    agent = SimpleNamespace(name="ConfigMiddlewareAgent", context_variables=current, _system_message="")
    inject_design_access_policy(agent, [])
    assert json.loads(agent._system_message.splitlines()[-1]) == [{
        "id": "coordinator", "permissions": ["reports.create_report", "reports.delete_report", "reports.update_report"],
    }]


def test_stock_request_collections_do_not_block_unrelated_notes_access(notes_with_request_packs):
    _, inputs = notes_with_request_packs
    policy = _request_policy()
    policy["grants"] = policy["grants"][:1]
    assert validate_access_policy(policy, **inputs) == policy
    roles = compiled_role_grants(
        policy, data_contract=inputs["data_contract"], pack_authorization=inputs["pack_authorization"],
    )
    assert roles[0] == {"id": "reader", "permissions": ["notes.get_notes", "notes.list_notes"]}
    assert all(not role["permissions"] for role in roles[1:])


@pytest.mark.parametrize("reverse", [False, True])
def test_bare_request_grant_reports_both_stock_owners(notes_with_request_packs, reverse):
    _, inputs = notes_with_request_packs
    if reverse:
        inputs["data_contract"]["surfaces"].reverse()
    policy = _request_policy()
    policy["grants"][1]["collection"] = "requests"
    with pytest.raises(ValueError) as rejected:
        validate_access_policy(policy, **inputs)
    assert "friends.requests" in str(rejected.value)
    assert "support.requests" in str(rejected.value)


def test_scoped_stock_request_grants_compile_only_the_reviewed_permissions(notes_with_request_packs):
    _, inputs = notes_with_request_packs
    policy = _request_policy()
    validated = validate_access_policy(policy, **inputs)
    assert validated == policy
    roles = compiled_role_grants(
        validated, data_contract=inputs["data_contract"], pack_authorization=inputs["pack_authorization"],
    )
    assert roles == [
        {"id": "reader", "permissions": ["notes.get_notes", "notes.list_notes"]},
        {"id": "member", "permissions": ["friends.manage"]},
        {"id": "moderator", "permissions": ["support.manage"]},
    ]
    invalid = deepcopy(policy)
    invalid["grants"][1]["actions"].remove("remove_friend")
    with pytest.raises(ValueError, match="shared pack permission"):
        validate_access_policy(invalid, **inputs)
    invalid = deepcopy(policy)
    invalid["grants"][2]["collection"] = "friends.requests"
    with pytest.raises(ValueError, match="update_support_status"):
        validate_access_policy(invalid, **inputs)


@pytest.mark.parametrize("reference", ["requests", "reports.requests", "friends.requests", "support.requests"])
def test_authored_and_pack_request_homonyms_are_not_grant_targets(notes_with_request_packs, reference):
    _, inputs = notes_with_request_packs
    group = next(s for s in inputs["data_contract"]["surfaces"] if s["surface_id"] == "reports")
    extra = deepcopy(group["collections"][0])
    extra.update(name="requests", entity="ApprovalRequest")
    group["collections"].append(extra)
    surface = next(s for s in inputs["design_surface_map"]["surfaces"] if s["surface_id"] == "reports")
    surface["primary_entities"].append("ApprovalRequest")
    policy = _request_policy()
    policy["grants"] = policy["grants"][:1]
    assert validate_access_policy(policy, **inputs) == policy
    valid_actions = {grant["collection"]: grant["actions"] for grant in _request_policy()["grants"]}
    actions = valid_actions.get(reference, ["get_requests", "list_requests"])
    policy["grants"] = [{"role_id": "reader", "collection": reference, "actions": actions}]
    with pytest.raises(ValueError) as rejected:
        validate_access_policy(policy, **inputs)
    assert "requests" in str(rejected.value)
    assert "authored" in str(rejected.value) or "reports.requests" in str(rejected.value)


@pytest.mark.parametrize("reference", ["notes", "reports.notes", "imports.notes"])
def test_two_authored_note_collections_require_unique_grant_names(notes_with_request_packs, reference):
    _, inputs = notes_with_request_packs
    reports = next(group for group in inputs["data_contract"]["surfaces"] if group["surface_id"] == "reports")
    imports = deepcopy(reports)
    imports["surface_id"] = "imports"
    imported_notes = imports["collections"][0]
    imported_notes["ownership"]["surface_id"] = "imports"
    imported_notes["entity"] = "ImportedNote"
    inputs["data_contract"]["surfaces"].append(imports)
    inputs["design_surface_map"]["surfaces"].append({
        "surface_id": "imports", "surface_kind": "module", "owner": "app", "primary_entities": ["ImportedNote"],
    })
    policy = _request_policy()
    note_grant = policy["grants"].pop(0)
    assert validate_access_policy(policy, **inputs) == policy
    note_grant["collection"] = reference
    policy["grants"].append(note_grant)
    with pytest.raises(ValueError) as rejected:
        validate_access_policy(policy, **inputs)
    assert "notes" in str(rejected.value)
    assert "authored" in str(rejected.value) or "reports.notes" in str(rejected.value)


def test_owner_review_and_authored_projection_keep_stock_request_owners(notes_with_request_packs, persistence, monkeypatch):
    bundle, inputs = notes_with_request_packs
    context = inventory._context(monetized=False)
    context.set("pack_selection", [row.model_dump() for row in inputs["pack_authorization"].identities])
    policy = _request_policy()
    notes = bundle["data_contract"]["surfaces"][0]["collections"][0]
    notes["lifecycle"]["write_mode"] = "module_action"
    policy["roles"].append({"role_id": "editor", "label": "Editor", "description": "Maintain notes."})
    note_writes = {"role_id": "editor", "collection": "notes", "actions": ["create_note", "update_note", "delete_note"]}
    policy["grants"].append(note_writes)
    bundle["access_policy"] = policy

    async def approve(_tool_id, payload, **kwargs):
        assert any("friends.requests" in row and "send_friend_request" in row for row in payload["checkpoints"])
        assert any("support.requests" in row and "update_support_status" in row for row in payload["checkpoints"])
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", AsyncMock(side_effect=approve))
    result = inventory._save(context, bundle)
    assert result["outcome"] == "saved", result
    summary = persistence[2].await_args.kwargs["summary_payload"]
    artifact = {
        "app_id": "reports-app", "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "current",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": summary}},
    }
    context.set("design_docs_artifact", artifact)
    assert resolve_design_access_policy(context) == policy
    assert resolve_authored_module_access_policy(context) == {**policy, "grants": [policy["grants"][0], note_writes]}

    current = context.snapshot()
    current.pop("data_contract", None)
    recorded = current["design_docs_artifact"]["commit_metadata"]["metadata"]["summary_payload"]
    receipt = deepcopy(recorded["access_policy_review"])
    assert receipt["status"] == "approved" and receipt["review_id"]
    assert receipt["pack_identities"] == [row.model_dump() for row in inputs["pack_authorization"].identities]
    agent = SimpleNamespace(name="ConfigMiddlewareAgent", context_variables=current, _system_message="")
    inject_design_access_policy(agent, [])
    assert json.loads(agent._system_message.splitlines()[-1]) == [
        {"id": "reader", "permissions": ["notes.get_notes", "notes.list_notes"]},
        {"id": "member", "permissions": ["friends.manage"]},
        {"id": "moderator", "permissions": ["support.manage"]},
        {"id": "editor", "permissions": ["notes.create_note", "notes.delete_note", "notes.update_note"]},
    ]
    assert recorded["access_policy_review"] == receipt and recorded["access_policy"] == policy
    approved_prompt = agent._system_message
    current["data_contract"] = {"surfaces": []}
    with pytest.raises(ValueError, match="does not match data_contract"):
        inject_design_access_policy(agent, [])
    assert agent._system_message == approved_prompt

    summary["access_policy"]["grants"][2]["collection"] = "friends.requests"
    context.set("design_docs_artifact", artifact)
    with pytest.raises(ValueError, match="review"):
        resolve_design_access_policy(context)


def test_selected_pack_permissions_require_whole_reviewed_group(social):
    _, authorization, data, surface_map, policy = social
    validated = validate_access_policy(policy, data_contract=data, design_surface_map=surface_map,
                                       pack_authorization=authorization)
    assert compiled_role_grants(validated, data_contract=data, pack_authorization=authorization) == [
        {"id": "member", "permissions": ["friends.manage"]},
    ]
    partial = deepcopy(policy)
    partial["grants"][0]["actions"].pop()
    with pytest.raises(ValueError, match="shared pack permission"):
        validate_access_policy(partial, data_contract=data, design_surface_map=surface_map,
                               pack_authorization=authorization)

    split = deepcopy(policy)
    split["roles"].append({"role_id": "other", "label": "Other", "description": "Another role."})
    moved = split["grants"][0]["actions"].pop()
    split["grants"].append({"role_id": "other", "collection": "requests", "actions": [moved]})
    with pytest.raises(ValueError, match="shared pack permission"):
        validate_access_policy(split, data_contract=data, design_surface_map=surface_map,
                               pack_authorization=authorization)


def test_roles_receive_only_their_own_reviewed_permission_groups(social):
    _, authorization, data, surface_map, policy = social
    policy = deepcopy(policy)
    policy["roles"].append({"role_id": "admin", "label": "Admin", "description": "Read friend records."})
    reads = [action for action, permissions in authorization.actions["friends"].items() if "friends.read" in permissions]
    policy["grants"].append({"role_id": "admin", "collection": "requests", "actions": reads})
    validated = validate_access_policy(policy, data_contract=data, design_surface_map=surface_map,
                                       pack_authorization=authorization)
    assert compiled_role_grants(validated, data_contract=data, pack_authorization=authorization) == [
        {"id": "member", "permissions": ["friends.manage"]},
        {"id": "admin", "permissions": ["friends.read"]},
    ]


def test_authored_permission_collision_cannot_unlock_unreviewed_pack_actions(social):
    _, authorization, data, surface_map, pack_policy = social
    data, surface_map = deepcopy(data), deepcopy(surface_map)
    data["surfaces"].append({
        "surface_id": "app_controls", "surface_kind": "module", "collections": [{
            "name": "friends", "scope": "app", "entity": "Control", "tenancy": "app_wide", "owner_field": None,
            "fields": [{"name": "id", "type": "string", "required": True}],
            "lifecycle": {"write_mode": "module_action"},
        }],
    })
    surface_map["surfaces"].append({
        "surface_id": "app_controls", "surface_kind": "module", "owner": "app",
        "owned_mutations": ["manage"],
    })
    authored = {"role_id": "member", "collection": "friends", "actions": [
        "create_control", "update_control", "delete_control", "manage",
    ]}
    policy = {"roles": deepcopy(pack_policy["roles"]), "grants": [authored]}
    with pytest.raises(ValueError, match="shared pack permission.*friends.manage"):
        validate_access_policy(policy, data_contract=data, design_surface_map=surface_map,
                               pack_authorization=authorization)

    # A different role reviewing the pack actions cannot authorize this role's collision.
    other = deepcopy(pack_policy["grants"][0])
    other["role_id"] = "other"
    policy["roles"].append({"role_id": "other", "label": "Other", "description": "Other user's access."})
    policy["grants"].append(other)
    with pytest.raises(ValueError, match="shared pack permission.*friends.manage"):
        validate_access_policy(policy, data_contract=data, design_surface_map=surface_map,
                               pack_authorization=authorization)

    policy["grants"].append(deepcopy(pack_policy["grants"][0]))
    validated = validate_access_policy(policy, data_contract=data, design_surface_map=surface_map,
                                       pack_authorization=authorization)
    manifest = {"modules/app_controls/module.yaml": yaml.safe_dump({
        "schema_version": "mozaiks.module.v1", "module": {"id": "app_controls", "handler": "backend.handler:Handler"},
        "permissions": [{"id": "friends.manage", "description": "Reviewed action."}],
        "actions": [{"id": "manage", "description": "Reviewed action.", "handler_method": "manage",
                     "permissions": ["friends.manage"]}],
    })}
    validate_pack_permission_consumers(manifest, validated, data_contract=data, pack_authorization=authorization)


def test_pack_source_change_refuses_recorded_identity(tmp_path, monkeypatch):
    root = tmp_path / "social"
    shutil.copytree(ROOT / "factory_app/build_context/social", root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    metadata = verify_pack_integrity(root, "social")
    context = {"capability_packs": [{
        "id": "social", "pack_source_path": str(root), "capability_source": "generated_module",
        "version": metadata["version"], "digest": metadata["digest"],
    }]}
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path))
    pins = pin_pack_choices(context, ["social"])
    manifest = root / "templates/modules/friends/module.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    with pytest.raises(PackIntegrityError, match="renew design review"):
        verified_pack_authorization(context, selection=pins)


def test_messaging_permission_uses_only_reviewed_real_pack_actions():
    root = ROOT / "factory_app/build_context/messaging"
    metadata = verify_pack_integrity(root, "messaging")
    authorization = _candidate_authorization({"capability_packs": [{
        "id": "messaging", "pack_source_path": str(root), "capability_source": "generated_module",
        "version": metadata["version"], "digest": metadata["digest"],
    }]})
    surface_map = {"surfaces": []}
    data = _compose_reviewed_pack_data({"version": "1", "surfaces": [], "shared_collections": []}, surface_map, authorization)
    _validate_design_collections(data, surface_map, pack_authorization=authorization)
    actions = [action for action, permissions in authorization.actions["messages"].items() if "messages.write" in permissions]
    assert set(actions) == {"create_thread", "send_message", "mark_thread_read"}
    policy = {
        "roles": [{"role_id": "member", "label": "Member", "description": "Use conversations."}],
        "grants": [{"role_id": "member", "collection": "threads", "actions": actions}],
    }
    validated = validate_access_policy(policy, data_contract=data, design_surface_map=surface_map,
                                       pack_authorization=authorization)
    assert compiled_role_grants(validated, data_contract=data, pack_authorization=authorization) == [
        {"id": "member", "permissions": ["messages.write"]},
    ]


def test_selected_pack_data_cannot_be_forged_or_replaced(social):
    _, authorization, data, surface_map, policy = social
    changed = deepcopy(data)
    next(group for group in changed["surfaces"] if group["surface_id"] == "friends")["collections"][0]["tenancy"] = "per_user"
    with pytest.raises(ValueError):
        validate_access_policy(policy, data_contract=changed, design_surface_map=surface_map,
                               pack_authorization=authorization)
    changed_surface = deepcopy(surface_map)
    next(row for row in changed_surface["surfaces"] if row["surface_id"] == "friends")["owner"] = "hosted"
    with pytest.raises(ValueError):
        _validate_design_collections(data, changed_surface, pack_authorization=authorization)

    extra = deepcopy(data)
    collection = deepcopy(next(group for group in data["surfaces"] if group["surface_id"] == "friends")["collections"][0])
    collection.update(name="injected", entity="Injected", ownership={"surface_id": "friends", "surface_kind": "module"})
    extra["shared_collections"].append(collection)
    with pytest.raises(ValueError, match="Unverified collection friends.injected"):
        _validate_design_collections(extra, surface_map, pack_authorization=authorization)


def test_design_review_binds_pack_actions_and_source_identity(social, persistence, monkeypatch):
    selection, authorization, _, _, policy = social
    context = inventory._context(monetized=False)
    context.set("capability_packs", selection["capability_packs"])
    context.set("pack_selection", [row.model_dump() for row in authorization.identities])
    bundle = inventory._bundle(pricing=False)
    bundle["access_policy"] = policy

    async def approve(_tool_id, payload, **kwargs):
        assert any("send_friend_request" in row and "Member" in row for row in payload["checkpoints"])
        assert any("Selected pack social version" in row for row in payload["checkpoints"])
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", AsyncMock(side_effect=approve))
    result = inventory._save(context, bundle)
    assert result["outcome"] == "saved", result
    summary = persistence[2].await_args.kwargs["summary_payload"]
    review = summary["access_policy_review"]
    assert review["status"] == "approved"
    assert review["pack_identities"] == [row.model_dump() for row in authorization.identities]
    context.set("design_docs_artifact", {
        "app_id": "reports-app", "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "current",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": summary}},
    })
    assert resolve_design_access_policy(context) == policy
    templates = resolve_managed_capability_templates(selection["capability_packs"], context_variables=context)
    assembled = _merge_code_files(
        [{"code_files": [*templates, {"filename": "app.json", "content": '{"authRequired": true}'}]}],
        data_contract=summary["data_contract"], design_surface_map=summary["surface_map"],
        context_variables=context,
    )
    files = {row["filename"]: row["content"] for row in assembled}
    roles = yaml.safe_load(files["config/auth.yaml"])["authorization"]["roles"]
    assert roles == [{"id": "member", "permissions": ["friends.manage"]}]
    forged = deepcopy(summary)
    forged["access_policy_review"]["pack_identities"][0]["digest"] = "sha256:" + "0" * 64
    context.set("design_docs_artifact", {
        "app_id": "reports-app", "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "current",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": forged}},
    })
    with pytest.raises(ValueError, match="pack identities"):
        resolve_design_access_policy(context)


def test_empty_review_does_not_authorize_pack_actions(social, persistence, monkeypatch):
    selection, _, _, _, policy = social
    context = inventory._context(monetized=False)
    context.set("capability_packs", selection["capability_packs"])
    ui = AsyncMock()
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    result = inventory._save(context, inventory._bundle(pricing=False))
    assert result["outcome"] == "saved", result
    ui.assert_not_awaited()
    summary = persistence[2].await_args.kwargs["summary_payload"]
    assert summary["access_policy_review"]["status"] == "not_required"
    summary["access_policy"] = policy
    context.set("design_docs_artifact", {
        "app_id": "reports-app", "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "current",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": summary}},
    })
    with pytest.raises(ValueError, match="pack identities"):
        resolve_design_access_policy(context)


@pytest.mark.parametrize("change", ["selection", "source"])
def test_changed_pack_during_owner_review_does_not_persist_approval(tmp_path, persistence, monkeypatch, change):
    root = tmp_path / "social"
    shutil.copytree(ROOT / "factory_app/build_context/social", root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path))
    metadata = verify_pack_integrity(root, "social")
    context = inventory._context(monetized=False)
    context.set("capability_packs", [{"id": "social", "pack_source_path": str(root),
                                    "capability_source": "generated_module", "version": metadata["version"],
                                    "digest": metadata["digest"]}])
    context.set("pack_selection", pin_pack_choices(context, ["social"]))

    async def approve(_tool_id, payload, **kwargs):
        if change == "selection":
            context.set("pack_selection", [])
        else:
            manifest = root / "templates/modules/friends/module.yaml"
            manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# changed while owner reviewed\n", encoding="utf-8")
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", AsyncMock(side_effect=approve))
    result = inventory._save(context, inventory._bundle(pricing=False))
    assert result["outcome"] == "blocked", result
    assert result["reason"] == "access_policy_review_failed", result
    persistence[1].assert_not_called()
    persistence[2].assert_not_awaited()


def test_changed_pack_after_owner_review_refuses_consumption(tmp_path, persistence, monkeypatch):
    root = tmp_path / "social"
    shutil.copytree(ROOT / "factory_app/build_context/social", root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    metadata = verify_pack_integrity(root, "social")
    selection = [{"id": "social", "pack_source_path": str(root), "capability_source": "generated_module",
                  "version": metadata["version"], "digest": metadata["digest"]}]
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path))
    authorization = _candidate_authorization({"capability_packs": selection})
    actions = [action for action, permissions in authorization.actions["friends"].items() if "friends.manage" in permissions]
    policy = {"roles": [{"role_id": "member", "label": "Member", "description": "Manage friends."}],
              "grants": [{"role_id": "member", "collection": "requests", "actions": actions}]}
    context = inventory._context(monetized=False)
    context.set("capability_packs", selection)
    context.set("pack_selection", [row.model_dump() for row in authorization.identities])
    bundle = inventory._bundle(pricing=False)
    bundle["access_policy"] = policy

    async def approve(_tool_id, payload, **kwargs):
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", AsyncMock(side_effect=approve))
    result = inventory._save(context, bundle)
    assert result["outcome"] == "saved", result
    summary = persistence[2].await_args.kwargs["summary_payload"]
    context.set("design_docs_artifact", {
        "app_id": "reports-app", "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "current",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": summary}},
    })
    assert resolve_design_access_policy(context) == policy
    manifest = root / "templates/modules/friends/module.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# modified after review\n", encoding="utf-8")
    with pytest.raises(PackIntegrityError, match="renew design review"):
        resolve_design_access_policy(context)


def test_unreviewed_module_cannot_consume_pack_permission(social):
    _, authorization, data, surface_map, policy = social
    policy = validate_access_policy(policy, data_contract=data, design_surface_map=surface_map,
                                    pack_authorization=authorization)
    forged = {"modules/other/module.yaml": yaml.safe_dump({
        "schema_version": "mozaiks.module.v1", "module": {"id": "other", "handler": "backend.handler:Handler"},
        "permissions": [{"id": "friends.manage", "description": "Forged permission."}],
        "actions": [{"id": "steal", "description": "Forged action.", "handler_method": "steal", "permissions": ["friends.manage"]}],
    })}
    with pytest.raises(ValueError, match="Unreviewed action"):
        validate_pack_permission_consumers(forged, policy, data_contract=data, pack_authorization=authorization)


def test_pack_permission_cannot_be_an_unauthenticated_default_scope(social):
    _, authorization, data, surface_map, policy = social
    baseline = {"app.json": '{"authRequired": true}'}
    baseline.update(materialize_auth_scaffold(baseline))
    config = yaml.safe_load(baseline["config/auth.yaml"])
    config["frontend"]["default_scopes"].append("friends.manage")
    baseline["config/auth.yaml"] = yaml.safe_dump(config)
    with pytest.raises(ValueError, match="default scopes"):
        materialize_auth_scaffold(baseline, data_contract=data, design_surface_map=surface_map,
                                  access_policy=policy, pack_authorization=authorization)


@pytest.mark.asyncio
async def test_compiled_role_controls_real_module_dispatch(social, monkeypatch):
    _, authorization, data, surface_map, policy = social
    files = materialize_auth_scaffold({"app.json": '{"authRequired": true}'}, data_contract=data,
                                     design_surface_map=surface_map, access_policy=policy,
                                     pack_authorization=authorization)
    roles = yaml.safe_load(files["config/auth.yaml"])["authorization"]["roles"]
    permissions = next(role["permissions"] for role in roles if role["id"] == "member")

    class Handler:
        calls = 0

        async def send_friend_request(self, ctx, **params):
            self.calls += 1
            return {"success": True}

    class Audit:
        async def log_module_action(self, **kwargs):
            return None

    handler = Handler()
    executor = ModuleExecutor()
    executor.register("friends", handler, action_method_map={"send_friend_request": "send_friend_request"},
                      action_permissions={"send_friend_request": ["friends.manage"]})
    registry = ExecutorRegistry()
    registry.register(executor)
    app = SimpleNamespace(state=SimpleNamespace(executor_registry=registry))
    module = importlib.import_module("mozaiksai.core.runtime.composition.module_executor")
    monkeypatch.setattr(module, "get_audit_logger", lambda: Audit())
    for scopes, allowed in [((), False), (("openid", "profile", "email"), False), (tuple(permissions), True)]:
        result = await dispatch_module_action(ModuleActionDispatchRequest(
            module="friends", action="send_friend_request",
            scope=ModuleDispatchScope(app_id="test", user_id="user-1"),
            authority=ModuleDispatchAuthority(kind="authenticated_user", permission_mode="enforce",
                reason="reviewed pack dispatch", actor_id="user-1", permissions=scopes),
        ), app=app)
        assert result.success is allowed
        if not allowed:
            assert result.error_code == "PERMISSION_DENIED" and handler.calls == 0
    assert handler.calls == 1


def test_selected_pack_without_grants_still_requires_owner_review(social, persistence, monkeypatch):
    _, authorization, _, _, _ = social
    context = inventory._context(monetized=False)
    context.set("pack_selection", [row.model_dump() for row in authorization.identities])

    async def approve(_tool, payload, **kwargs):
        assert any("Selected pack social" in checkpoint for checkpoint in payload["checkpoints"])
        return {"action": "approve", "approved": True, "review_id": payload["review_id"]}

    ui = AsyncMock(side_effect=approve)
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    result = inventory._save(context, inventory._bundle(pricing=False))
    assert result["outcome"] == "saved", result
    ui.assert_awaited_once()
    summary = persistence[2].await_args.kwargs["summary_payload"]
    assert summary["access_policy"]["grants"] == []
    assert summary["access_policy_review"]["status"] == "approved"
    summary["access_policy_review"]["status"] = "not_required"
    context.set("design_docs_artifact", {
        "app_id": "reports-app", "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "current",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": summary}},
    })
    with pytest.raises(ValueError, match="explicit owner approval"):
        resolve_design_access_policy(context)
