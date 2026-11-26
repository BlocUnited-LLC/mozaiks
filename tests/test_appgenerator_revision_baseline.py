import hashlib
import importlib
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
import yaml

from factory_app.workflows._shared import revision_app_source
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.platform.genesis_import import GenesisImportError
from factory_app.workflows.AppGenerator.tools import (
    app_validation,
    export_app_code,
    generate_and_download,
)
from factory_app.workflows.AppGenerator.tools import hydrate_app_revision_context as revision
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    _verified_revision_source,
    assemble_app_tasks,
)
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.generate_and_download import (
    _build_generated_files_manifest,
)
from factory_app.workflows.AppGenerator.tools.save_admin_registry import save_admin_registry
from factory_app.workflows.AppGenerator.tools.task_integrity import artifact_snapshot_digest
from mozaiksai.control_plane.contracts import build_acceptance_evidence
from mozaiksai.core.artifacts import content_store
from mozaiksai.core.artifacts.models import BuildRecord, resolve_canonical_bundle_entry
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_revision import scoped_service_revision_paths
from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    parse_task_batches_config,
)


@pytest.mark.parametrize("role", ["DesignDocsAgent", "DesignPackSelectionAgent"])
async def test_design_prompt_projects_the_original_accepted_actions_after_an_upstream_export(baseline, role):
    from factory_app.workflows._shared.pack_selection import pin_pack_choices
    from factory_app.workflows.DesignDocs.tools.hook_accepted_module_actions import (
        inject_accepted_module_actions,
    )
    from mozaiksai.core.workflow.execution.middleware import load_prompt_middleware_entries

    assert any(entry["agent"] == role and entry["function"] == "inject_accepted_module_actions"
               for entry in load_prompt_middleware_entries("DesignDocs", base_path="factory_app/workflows"))

    class Capture:
        name = role
        system_message = "Design this app."

        def update_system_message(self, message):
            self.system_message = message

    manifest = {
        "schema_version": "mozaiks.module.v1", "module": {"id": "records", "handler": "backend.handler:RecordsHandler"},
        "actions": [{
            "id": "create_record", "handler_method": "create_record", "description": "A private source description.",
            "input_schema": {"type": "object", "properties": {"body": {"type": "string"}, "label": {"type": "string"}},
                             "required": ["body"]},
            "output_schema": {"type": "object", "properties": {"record_id": {"type": "string"}, "summary": {"type": "string"}}},
        }],
    }
    _replace_archive(baseline, {"modules/records/module.yaml": yaml.safe_dump(manifest), "data/contract.json": "[]"})
    baseline.artifact.lifecycle_status = "current"
    baseline.artifact.validation_status = "passed"
    [accepted_pack] = pin_pack_choices(baseline.context, ["files"])
    baseline.artifact.commit_metadata.metadata["app_bundle_acceptance"] = {
        "resolved_capability_packs": [{**accepted_pack, "source": "local_component"}],
    }
    agent = Capture()
    agent.context_variables = {**baseline.context, "workflow_sequence": "full_rebuild", "artifact_version_id": "upstream-export",
                               "refinement_request_meta": {"build_family": "app_bundle", "build_record_id": baseline.artifact.id}}
    await inject_accepted_module_actions(agent, [])
    assert "Accepted app record artifact_1" in agent.system_message
    assert f"sha256:{baseline.artifact.files_manifest[0].sha256}" in agent.system_message
    rows = [json.loads(line) for line in agent.system_message.splitlines() if line.startswith("{")]
    if role == "DesignPackSelectionAgent":
        pack, module = rows
        assert pack == accepted_pack
        assert module == {"module": "records", "actions": ["create_record"]}
        assert "source_path" not in agent.system_message
        baseline.artifact.commit_metadata.metadata["app_bundle_acceptance"]["resolved_capability_packs"] = [{"digest": "invalid"}]
        await inject_accepted_module_actions(agent, [])
        assert "could not be verified" in agent.system_message and "create_record" not in agent.system_message
        baseline.artifact.commit_metadata.metadata["app_bundle_acceptance"]["resolved_capability_packs"] = []
    else:
        [row] = [entry for entry in rows if "action" in entry]
        assert {"schema_page_inventory": "unavailable"} in rows
        assert {"collection_inventory": "unavailable"} in rows
        assert row["module"] == "records" and row["action"] == "create_record"
        assert row["input"] == {"type": "object", "fields": {"body": "string", "label": "string"}, "required": ["body"]}
        assert set(row["output"]["fields"]) == {"record_id", "summary"}
    assert "private source description" not in agent.system_message and "ORIGINAL" not in agent.system_message
    digest = baseline.artifact.files_manifest[0].sha256
    for failure in ("wrong_owner", "bad_archive", "unaccepted"):
        agent.system_message = "Design this app."
        agent.context_variables["user_id"] = "other" if failure == "wrong_owner" else "owner"
        baseline.artifact.validation_status = "failed" if failure == "unaccepted" else "passed"
        baseline.artifact.files_manifest[0].sha256 = "0" * 64 if failure == "bad_archive" else digest
        await inject_accepted_module_actions(agent, [])
        assert "could not be verified" in agent.system_message and "create_record" not in agent.system_message
    agent.system_message = "Design this app."
    agent.context_variables = {"build_mode": "genesis"}
    await inject_accepted_module_actions(agent, [])
    assert agent.system_message == "Design this app."


@pytest.mark.parametrize("closed", [True, False])
async def test_revision_design_reads_accepted_collections_routes_and_client_inputs(scoped_module_revision, closed):
    from factory_app.workflows.DesignDocs.tools.hook_accepted_module_actions import (
        inject_accepted_module_actions,
    )

    baseline = scoped_module_revision.baseline
    baseline.artifact.lifecycle_status = "current"
    baseline.artifact.validation_status = "passed"
    manifest = yaml.safe_load(baseline.files["modules/records/module.yaml"])
    create = manifest["actions"][0]
    create["description"] = "OMIT_PRIVATE_VALUE"
    create["input_schema"] = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
    if closed:
        create["input_schema"]["additionalProperties"] = False
    manifest["actions"].append({**create, "id": "update_record", "handler_method": "update_record", "api_surface": "internal"})
    data = json.loads(baseline.files["data/contract.json"])
    collection = data["surfaces"][0]["collections"][0]
    collection["fields"].extend([
        {"name": "created_at", "type": "datetime", "required": True},
        {"name": "suggestion", "type": "string", "required": False, "default": "OMIT_PRIVATE_VALUE"},
    ])
    page = yaml.safe_load(baseline.files["ui/pages/records.yaml"])
    page["roles"] = ["editor"]
    page["meta"] = {"requiresAuth": True, "requiresRole": "editor",
                    "routeAuth": {"module": "records", "action": "can_view", "params": {"key": "OMIT_PRIVATE_VALUE"}}}
    auth = yaml.safe_load((Path(__file__).parents[1] / "factory_app/app/config/auth.yaml").read_text(encoding="utf-8"))
    auth["authorization"] = {"roles": [{"id": "editor", "permissions": ["records.read"]}]}
    auth["routes"]["post_login_default"] = "/records?filter=OMIT_PRIVATE_VALUE"
    _replace_archive(baseline, {
        "modules/records/module.yaml": yaml.safe_dump(manifest), "config/auth.yaml": yaml.safe_dump(auth),
        "data/contract.json": json.dumps(data), "ui/pages/records.yaml": yaml.safe_dump(page),
    })
    context = {**baseline.context, "refinement_request_meta": {"build_family": "app_bundle", "build_record_id": baseline.artifact.id}}
    agent = SimpleNamespace(name="DesignDocsAgent", context_variables=context, system_message="")
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    await inject_accepted_module_actions(agent, [])
    assert "OMIT_PRIVATE_VALUE" not in agent.system_message
    rows = [json.loads(line) for line in agent.system_message.splitlines() if line.startswith("{")]
    [row] = [r for r in rows if "collection" in r]
    assert (row["module"], row["collection"], row["entity"], row["scope"], row["tenancy"],
            row["owner_field"], row["record_id_field"]) == ("records", "records", "Record", "app", "per_user", "user_id", "id")
    assert row["fields"] == [{"name": f["name"], "type": f["type"], "required": f["required"], "nullable": False}
                             for f in collection["fields"]]
    assert row["http_write_actions"] == [
        {"operation": "create", "action": "create_record", "closed_client_fields": ["title"] if closed else None}]
    assert {r["action"] for r in rows if "action" in r} == {"create_record", "update_record"}
    [access] = [r for r in rows if "auth_required" in r]
    assert access["auth_required"] and access["roles"] == auth["authorization"]["roles"]
    assert access["auth_routes"]["post_login_default"] == "/records"
    assert {"schema_page": "records", "route": "/records", "roles": ["editor"], "requires_auth": True,
            "requires_role": "editor", "route_auth": {"module": "records", "action": "can_view"}} in rows


async def test_pack_selector_cannot_relabel_an_accepted_generated_module_after_install(baseline, tmp_path, monkeypatch):
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        install_verified_local_pack,
    )
    from factory_app.workflows.DesignDocs.tools.hook_accepted_module_actions import (
        inject_accepted_module_actions,
    )

    baseline.artifact.lifecycle_status = "current"
    baseline.artifact.validation_status = "passed"
    metadata = baseline.artifact.commit_metadata.metadata
    metadata["capability_packs"] = [{"capability_pack_id": "greetings", "capability_source": "generated_module"}]
    metadata["app_bundle_acceptance"] = {"resolved_capability_packs": []}
    context = {**baseline.context, "build_context_root": str(tmp_path),
               "refinement_request_meta": {"build_family": "app_bundle", "build_record_id": baseline.artifact.id}}
    agent = SimpleNamespace(name="DesignPackSelectionAgent", context_variables=context, system_message="")
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path))
    await inject_accepted_module_actions(agent, [])
    before = agent.system_message
    assert "Verified reusable packs: 0" in before

    install_verified_local_pack(pack_source_path=Path(__file__).parent / "fixtures/community_packs/greetings",
                                build_context_root=tmp_path)
    await inject_accepted_module_actions(agent, [])
    assert agent.system_message == before

    del metadata["app_bundle_acceptance"]
    await inject_accepted_module_actions(agent, [])
    assert "no recorded pack inventory" in agent.system_message and '"pack_id"' not in agent.system_message


def _replace_archive(baseline, additions):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in {**baseline.files, **additions}.items():
            archive.writestr(path if path.startswith("other/") else "bundle/" + path, content)
    raw = buffer.getvalue()
    baseline.content.get_bundle.return_value = raw
    baseline.artifact.files_manifest[0].sha256 = hashlib.sha256(raw).hexdigest()


async def test_existing_compute_field_cannot_silently_become_a_canonical_input(scoped_module_revision):
    from factory_app.workflows.DesignDocs.tools.hook_accepted_module_actions import (
        validate_implicit_revision_writes,
    )

    baseline = scoped_module_revision.baseline
    context = {**baseline.context, "refinement_request_meta": {"build_family": "app_bundle", "build_record_id": baseline.artifact.id}}
    baseline.artifact.lifecycle_status = "current"
    baseline.artifact.validation_status = "passed"
    path = "modules/records/module.yaml"
    manifest = yaml.safe_load(baseline.files[path])
    action = manifest["actions"][0]
    action.update(description="Create a record", input_schema={
        "type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False,
    })
    _replace_archive(baseline, {path: yaml.safe_dump(manifest)})
    proposal = json.loads(baseline.files["data/contract.json"])
    collection = proposal["surfaces"][0]["collections"][0]
    collection["fields"].append({"name": "summary", "type": "string", "required": False})
    with pytest.raises(ValueError, match=r"create_record.*Set lifecycle.module_writes explicitly"):
        await validate_implicit_revision_writes(context, proposal, set())
    collection["lifecycle"]["module_writes"] = [{"operation": "create", "fields": ["title"]}]
    await validate_implicit_revision_writes(context, proposal, set())


async def test_implicit_revision_preserves_the_full_accepted_write_schema(scoped_module_revision):
    from factory_app.workflows.DesignDocs.tools.hook_accepted_module_actions import (
        validate_implicit_revision_writes,
    )

    baseline = scoped_module_revision.baseline
    baseline.artifact.lifecycle_status = "current"
    baseline.artifact.validation_status = "passed"
    context = {**baseline.context, "refinement_request_meta": {"build_family": "app_bundle", "build_record_id": baseline.artifact.id}}
    proposal = json.loads(baseline.files["data/contract.json"])
    title = next(f for f in proposal["surfaces"][0]["collections"][0]["fields"] if f["name"] == "title")
    title["enum"] = ["public", "private"]
    path = "modules/records/module.yaml"
    manifest = yaml.safe_load(baseline.files[path])
    title_schema = {"type": "string", "enum": ["private", "public"]}
    key_schema = {"type": "string"}
    signatures = [
        ("create_record", {"title": title_schema}, ["title"]),
        ("update_record", {"title": title_schema, "id": key_schema}, ["id"]),
        ("delete_record", {"id": key_schema}, ["id"]),
    ]
    manifest["actions"] = [{
        "id": name, "description": name, "handler_method": name, "permissions": [], "emits": [],
        "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
    } for name, props, required in signatures]
    _replace_archive(baseline, {path: yaml.safe_dump(manifest)})
    await validate_implicit_revision_writes(context, proposal, set())

    del title["enum"]
    with pytest.raises(ValueError, match="create_record.*Set lifecycle.module_writes explicitly"):
        await validate_implicit_revision_writes(context, proposal, set())
    title["enum"] = ["public", "private"]
    title["required"] = False
    with pytest.raises(ValueError, match="create_record.*Set lifecycle.module_writes explicitly"):
        await validate_implicit_revision_writes(context, proposal, set())
    title["required"] = True
    manifest["actions"][0]["input_schema"]["additionalProperties"] = True
    _replace_archive(baseline, {path: yaml.safe_dump(manifest)})
    with pytest.raises(ValueError, match="create_record.*Set lifecycle.module_writes explicitly"):
        await validate_implicit_revision_writes(context, proposal, set())


@pytest.fixture
def baseline(monkeypatch):
    files = {
        "app.json": json.dumps({"appId": "customer-app", "appName": "Contact Desk"}),
        "modules/records/backend/handler.py": "UNCHANGED\n",
        "modules/records/backend/service.py": "ORIGINAL\n",
        "ui/pages/records.yaml": "page_type: record_list\n",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr("bundle/" + path, content)
    raw = buffer.getvalue()
    content = SimpleNamespace(backend_name="memory", get_bundle=AsyncMock(return_value=raw))
    artifact = BuildRecord(
        id="artifact_1", app_id="customer-app", build_family="app_bundle",
        build_key="app_bundle", version_number=1, lineage_root_id="artifact_1",
        lifecycle_status="stale",
        files_manifest=[{
            "path": "bundle/bundle.zip", "sha256": hashlib.sha256(raw).hexdigest(),
            "content_type": "application/zip",
        }],
        commit_metadata={"author_user_id": "owner", "metadata": {
            "target_app_id": "customer-app", "build_registry_id": "registry_1",
            "build_id": "original-build", "phase": "genesis", "bundle_name": "bundle",
            "content_ref": "immutable-archive", "content_backend": "memory",
            "workspace_dir": "never-read-the-mutable-workspace",
        }},
    )
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=artifact))
    monkeypatch.setattr("mozaiksai.core.artifacts.store.get_build_record_store", lambda: store)
    monkeypatch.setattr(content_store, "get_artifact_content_store", lambda: content)
    context = {
        "app_id": "factory-host", "user_id": "owner", "build_mode": "revision",
        "workflow_sequence": "app_revision", "artifact_version_id": "artifact_1",
        "run_build_binding": {
            "target_app_id": "customer-app", "build_registry_id": "registry_1",
            "build_id": "new-build", "phase": "refinement",
        },
    }
    return SimpleNamespace(files=files, context=context, artifact=artifact, store=store, content=content)


@pytest.fixture
def scoped_module_revision(baseline):
    """An admitted create-only module whose refinement owns two implementation files."""
    backend = "modules/records/backend"
    contract = {"version": "1", "surfaces": [{
        "surface_id": "records", "surface_kind": "module", "collections": [{
            "name": "records", "entity": "Record", "scope": "app", "tenancy": "per_user",
            "owner_field": "user_id", "ownership": {"surface_id": "records", "surface_kind": "module"},
            "fields": [{"name": name, "type": "string", "required": True} for name in ("id", "title", "user_id")],
            "lifecycle": {"write_mode": "module_action", "migration_policy": "additive_only"},
        }],
    }]}
    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "records", "route": "/records",
        "title": "Records", "page_type": "settings", "layout": "full-width",
        "sections": [{"id": "header", "primitive": "PageHeader", "config": {"title": "Records"}}],
    }
    baseline.files.update({
        "modules/records/module.yaml": yaml.safe_dump({
            "schema_version": "mozaiks.module.v1",
            "module": {"id": "records", "handler": "backend.handler:RecordsHandler"},
            "actions": [{"id": "create_record", "handler_method": "create_record", "permissions": [], "emits": []}],
        }, sort_keys=False),
        f"{backend}/handler.py": (
            "from . import service\n\nclass RecordsHandler:\n"
            "    async def create_record(self, ctx, **params):\n"
            "        return await service.create_record(ctx, params['title'])\n"
        ),
        f"{backend}/service.py": (
            "from . import repo\n\nasync def create_record(ctx, title):\n"
            "    return {'item': await repo.create_record(ctx, {'title': title})}\n"
        ),
        f"{backend}/repo.py": (
            "from . import policy\n\nasync def create_record(ctx, values):\n"
            "    record = {**values, **policy.owner_values(ctx)}\n"
            "    await ctx.persistence.collection('records', 'records').insert_one(record)\n"
            "    return record\n"
        ),
        f"{backend}/policy.py": "def owner_values(ctx):\n    return {'user_id': ctx.user_id}\n",
        f"{backend}/schemas.py": "from typing import TypedDict\n\nclass Record(TypedDict):\n    title: str\n    user_id: str\n",
        "data/contract.json": json.dumps(contract),
        "ui/pages/records.yaml": yaml.safe_dump(page, sort_keys=False),
    })
    _replace_archive(baseline, {})
    authored = {
        f"{backend}/repo.py": baseline.files[f"{backend}/repo.py"].replace(
            "record = {**values, **policy.owner_values(ctx)}", "record = {**values, **policy.owner_values(ctx), 'id': 'authored-record'}",
        ),
        f"{backend}/service.py": baseline.files[f"{backend}/service.py"].replace("'title': title", "'title': title.strip()"),
    }
    task = {
        "task_id": "repair_record_creation", "task_type": "business_services", "capability_pack_id": "records",
        "surface_id": "records", "surface_kind": "module", "execution_target": "AppGenerator",
        "initial_agent": "ServiceAgent", "initial_message": "Normalize record titles and assign record IDs.",
        "owned_paths": list(authored), "depends_on": [],
    }
    plan = {
        "pages": [page], "build_tasks": [task], "capability_packs": [{
            "capability_pack_id": "records", "capability_source": "generated_module", "primary_entities": ["Record"],
        }],
    }
    context = create_context_container(initial={
        **baseline.context, "app_build_plan": plan, "app_task_batch_items": [task], "data_contract": contract,
    })
    return SimpleNamespace(baseline=baseline, context=context, authored=authored, task=task, plan=plan, contract=contract)


async def _run_module_revision_task(case, *, output=None):
    candidate = output if output is not None else {
        "python_files": [{
            "path": path, "kind": Path(path).stem, "purpose": "Correct record creation.",
            "contract_refs": ["records.create_record"], "content": source,
        } for path, source in case.authored.items()],
        "agent_message": "Updated the two existing implementation files.",
    }

    class Worker:
        async def ask(self, message, **kwargs):
            return SimpleNamespace(body=json.dumps(candidate))

    config = parse_task_batches_config({"version": 1, "batches": [{
        "id": "app_build_tasks", "trigger_agent": "AppPlanAgent",
        "source": {"kind": "context_variable", "path": "app_task_batch_items", "task_model": "AppBuildTask"},
        "result": {"context_key": "app_task_batch_results", "status_key": "app_task_batch_status", "require_owned_paths": True},
    }]})
    snapshot = case.context.to_dict()
    await execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"ServiceAgent": Worker()}, context_variables=snapshot, fresh_agents_per_task=False,
    )
    for key in ("app_task_batch_results", "app_task_batch_status"):
        case.context.set(key, snapshot[key])
    return snapshot


@pytest.mark.asyncio
async def test_scoped_module_revision_admits_authored_two_file_patch(scoped_module_revision):
    case = scoped_module_revision
    hydrated = await revision.hydrate_app_revision_context(case.context)
    assert hydrated["status"] == "hydrated"
    snapshot = await _run_module_revision_task(case)
    assert snapshot["app_task_batch_status"] == "completed"
    output = snapshot["app_task_batch_results"][case.task["task_id"]]
    assert extract_code_file_map_from_payload(output) == case.authored
    assert detach(case.context.get("generated_files")) == case.baseline.files


@pytest.mark.asyncio
async def test_scoped_module_revision_assembly_preserves_create_only_contract_and_admin_registry(scoped_module_revision):
    case = scoped_module_revision
    case.context.set("app_task_batch_results", {case.task["task_id"]: {
        "code_files": [{"filename": path, "content": source} for path, source in case.authored.items()],
    }})
    save_admin_registry(admin_registry={"schema_version": "mozaiks.admin.registry.v1", "pages": [{
        "id": "overview", "label": "Overview", "path": "/admin", "icon": "dashboard",
        "scope": "app", "order": 0, "enabled": True,
    }]}, context_variables=case.context)
    registry = detach(case.context.get("admin_registry"))
    result = await assemble_app_tasks(context_variables=case.context)
    assert result["success"], result
    files = extract_code_file_map_from_payload(result)
    for path, source in case.baseline.files.items():
        if path not in {*case.authored, "app.json", "data/contract.json"}:
            assert files[path] == source, path
    assert {path: files[path] for path in case.authored} == case.authored
    assert yaml.safe_load(files["modules/records/module.yaml"])["actions"] == [{
        "id": "create_record", "handler_method": "create_record", "permissions": [], "emits": [],
    }]
    assert json.loads(files["data/contract.json"]) == case.contract
    assert files["data/contract.json"] == case.baseline.files["data/contract.json"]
    assert yaml.safe_load(files["admin/admin_registry.yaml"]) == registry
    assert case.baseline.content.get_bundle.await_count >= 1


@pytest.mark.asyncio
async def test_scoped_module_revision_preserves_compiled_partial_pack(scoped_module_revision):
    from factory_app.workflows._shared.pack_selection import PhysicalPackContext, selected_pack_rows
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        scan_generated_bundle,
    )
    from factory_app.workflows.AppGenerator.tools.module_entitlement_gates import (
        apply_entitlement_gates,
    )
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )
    from tests.reviewed_pack_fixture import reviewed_pack_fixture
    from tests.test_capability_pack_proof import _fixture_inputs

    case = scoped_module_revision
    pack_id = "outcome_feedback"
    seed, _ = _fixture_inputs(pack_id)
    contract = {**case.contract, "surfaces": case.contract["surfaces"] + seed["data_contract"]["surfaces"]}
    plan = {**case.plan, "capability_packs": case.plan["capability_packs"] + seed["app_build_plan"]["capability_packs"]}
    case.baseline.artifact.commit_metadata.author_user_id = "fixture-owner"
    context = create_context_container(initial=reviewed_pack_fixture({
        **case.baseline.context,
        "user_id": "fixture-owner",
        "app_build_plan": plan,
        "data_contract": contract,
        "app_task_batch_items": [case.task],
        "app_task_batch_results": {case.task["task_id"]: {
            "code_files": [{"filename": path, "content": source} for path, source in case.authored.items()],
        }},
    }, [pack_id]))
    pack_context = PhysicalPackContext(context)
    packs = selected_pack_rows(context)
    templates = resolve_managed_capability_templates(packs, context_variables=pack_context)
    baseline_sources = [*templates, {"filename": "data/contract.json", "content": json.dumps(contract)}]
    compiled = _merge_code_files([{"code_files": baseline_sources}], app_build_plan=plan,
                                 data_contract=contract, context_variables=pack_context)
    compiled = apply_entitlement_gates(compiled, context_variables=context)
    compiled_files = {item["filename"]: item["content"] for item in compiled}
    pack_service = "modules/outcome_feedback/backend/service.py"
    assert compiled_files[pack_service] != next(item["content"] for item in templates if item["filename"] == pack_service)
    case.baseline.files.update(compiled_files)
    _replace_archive(case.baseline, {})

    result = await assemble_app_tasks(context_variables=context)

    assert result["success"], result
    files = extract_code_file_map_from_payload(result)
    assert {path: files[path] for path in case.authored} == case.authored
    assert files["modules/records/module.yaml"] == case.baseline.files["modules/records/module.yaml"]
    assert files["data/contract.json"] == case.baseline.files["data/contract.json"]
    compiled_pack_files = {path: source for path, source in compiled_files.items()
                           if path.startswith("modules/outcome_feedback/")}
    assert {path: files[path] for path in compiled_pack_files} == compiled_pack_files
    errors = scan_generated_bundle(files, capability_packs=packs, planned_data_contract=contract,
                                   context_variables=pack_context)
    assert not errors, errors


@pytest.mark.asyncio
async def test_scoped_module_revision_repair_saves_authored_patch_without_synthesizing_reads(scoped_module_revision):
    case = scoped_module_revision
    await revision.hydrate_app_revision_context(case.context)
    case.context.set("bundle_repair_target", "ServiceAgent")
    case.context.set("bundle_repair_result", {"active": {
        "task_id": case.task["task_id"], "target_agent": "ServiceAgent", "request_id": "record-repair",
        "allowed_paths": list(case.authored), "status": "selected",
    }})
    result = save_generated_code(StructuredOutputOverlay(case.context, {
        "python_files": [{
            "path": path, "kind": Path(path).stem, "purpose": "Correct record creation.",
            "contract_refs": ["records.create_record"], "content": source,
        } for path, source in case.authored.items()],
    }))
    assert result["saved_files"] == sorted(case.authored), result
    assert result["deleted_files"] == []
    assert extract_code_file_map_from_payload({"code_files": detach(case.context.get("code_files"))}) == case.authored
    assert detach(case.context.get("generated_files")) == case.baseline.files
    assert case.context.get("bundle_repair_result")["active"]["status"] == "responded"


@pytest.mark.asyncio
async def test_scoped_module_revision_rejects_data_change_without_contract_owner(scoped_module_revision):
    case = scoped_module_revision
    changed = deepcopy(case.contract)
    changed["surfaces"][0]["collections"][0]["fields"].append({
        "name": "category", "type": "string", "required": True,
    })
    case.context.set("data_contract", changed)
    case.context.set("app_task_batch_results", {case.task["task_id"]: {
        "code_files": [{"filename": path, "content": source} for path, source in case.authored.items()],
    }})
    result = await assemble_app_tasks(context_variables=case.context)
    assert result["success"] is False, result
    assert "changed the carried data contract" in result["error"]


@pytest.mark.parametrize("malformed", ["tasks_not_list", "task_not_object", "paths_not_list", "path_not_string"])
def test_malformed_task_scope_never_selects_module_preservation(scoped_module_revision, malformed):
    case = scoped_module_revision
    plan = deepcopy(case.plan)
    if malformed == "tasks_not_list":
        plan["build_tasks"] = {"task_id": case.task["task_id"]}
    elif malformed == "task_not_object":
        plan["build_tasks"] = [None]
    elif malformed == "paths_not_list":
        plan["build_tasks"][0]["owned_paths"] = "modules/records/backend/repo.py"
    else:
        plan["build_tasks"][0]["owned_paths"] = [None]
    case.context.set("app_build_plan", plan)
    assert scoped_service_revision_paths(case.context, case.baseline.files) == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["task_output", "repair_overlay", "hydrated_files", "deletion"])
async def test_scoped_module_revision_assembly_rejects_unowned_existing_file_change(scoped_module_revision, lane):
    case = scoped_module_revision
    await revision.hydrate_app_revision_context(case.context)
    foreign = "modules/records/backend/handler.py"
    foreign_source = case.baseline.files[foreign].replace("params['title']", "'unapproved title'")
    output = {"code_files": [{"filename": path, "content": source} for path, source in case.authored.items()]}
    if lane == "task_output":
        output["code_files"].append({"filename": foreign, "content": foreign_source})
    elif lane == "repair_overlay":
        case.context.set("code_files", [{"filename": foreign, "content": foreign_source}])
    elif lane == "hydrated_files":
        case.context.set("generated_files", {**case.baseline.files, foreign: foreign_source})
    else:
        case.context.set("deleted_files", [foreign])
    case.context.set("app_task_batch_results", {case.task["task_id"]: output})
    result = await assemble_app_tasks(context_variables=case.context)
    assert result["success"] is False, result
    assert foreign in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,error", [
    ("syntax", "service.py"), ("motor_api", "find"), ("unowned_file", "outside owned_paths"),
])
async def test_scoped_module_revision_admission_keeps_source_and_ownership_checks(scoped_module_revision, fault, error):
    case = scoped_module_revision
    await revision.hydrate_app_revision_context(case.context)
    if fault == "syntax":
        case.authored["modules/records/backend/service.py"] = "async def create_record(ctx, title)\n    return title\n"
    elif fault == "motor_api":
        case.authored["modules/records/backend/repo.py"] = case.authored["modules/records/backend/repo.py"].replace(
            ".insert_one(record)", ".find({'title': record['title']}).to_list(length=10)",
        )
    else:
        case.authored["modules/records/backend/handler.py"] = case.baseline.files["modules/records/backend/handler.py"] + "# unapproved edit\n"
    with pytest.raises(RuntimeError, match=error):
        await _run_module_revision_task(case)
    assert detach(case.context.get("generated_files")) == case.baseline.files


@pytest.mark.parametrize("scope", ["greenfield", "contract_owner", "data_owner", "new_implementation", "empty_plan"])
def test_non_scoped_module_assembly_retains_canonical_synthesis(scoped_module_revision, scope):
    case = scoped_module_revision
    baseline_files = dict(case.baseline.files)
    plan = deepcopy(case.plan)
    if scope == "greenfield":
        case.context.set("build_mode", "new")
        case.context.set("workflow_sequence", "app_generation")
    elif scope in {"contract_owner", "data_owner"}:
        path = "modules/records/module.yaml" if scope == "contract_owner" else "data/contract.json"
        plan["build_tasks"].append({
            "task_id": "new_contract", "task_type": "module_contract" if scope == "contract_owner" else "data_contract",
            "owned_paths": [path],
        })
    elif scope == "new_implementation":
        baseline_files.pop("modules/records/backend/repo.py")
    else:
        plan["build_tasks"] = []
    case.context.set("app_build_plan", plan)
    case.context.set("generated_files", baseline_files)
    files = {**baseline_files, **case.authored}
    # Ordinary generation leaves code-owned layers to their canonical renderers.
    files.pop("modules/records/backend/policy.py")
    files.pop("modules/records/backend/schemas.py")
    merged = _merge_code_files(
        [{"code_files": [{"filename": path, "content": source} for path, source in files.items()]}],
        app_build_plan=plan, data_contract=case.contract, context_variables=case.context,
        revision_baseline=baseline_files,
    )
    actions = yaml.safe_load(extract_code_file_map_from_payload({"code_files": merged})["modules/records/module.yaml"])["actions"]
    assert {"create_record", "update_record", "delete_record", "get_records", "list_records"} <= {action["id"] for action in actions}


@pytest.mark.asyncio
@pytest.mark.parametrize("container", [False, True])
async def test_revision_reads_verified_archive_and_preserves_resumed_changes(baseline, container):
    context = baseline.context
    context["generated_files"] = {"modules/records/backend/service.py": "REPAIRED\n"}
    context["deleted_files"] = ["ui/pages/records.yaml"]
    if container:
        context = create_context_container(initial=context)
    result = await revision.hydrate_app_revision_context(context)
    assert result["status"] == "hydrated"
    assert context.get("generated_files") == {
        "app.json": baseline.files["app.json"],
        "modules/records/backend/handler.py": "UNCHANGED\n",
        "modules/records/backend/service.py": "REPAIRED\n",
    }
    baseline.store.get_build_record.assert_awaited_once_with(
        app_id="customer-app", build_record_id="artifact_1",
    )
    baseline.content.get_bundle.assert_awaited_once_with("immutable-archive")
    assert context.get("app_id") == "factory-host"


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence", ["app_revision", "theme_revision", "theme_patch", "full_rebuild", "conceptual_replan"])
async def test_revision_uses_selected_app_theme_unless_the_sequence_recaptures_it(baseline, sequence):
    accepted = {"assets": {"logo": "selected.png"}, "colors": {"accent": {"main": "#D97706"}}}
    recaptured = {"colors": {"accent": {"main": "#2DD4BF"}}}
    _replace_archive(baseline, {"brand/theme_config.json": json.dumps(accepted)})
    baseline.context.update(artifact_kind="app_bundle", workflow_sequence=sequence, captured_theme_config=recaptured)
    await revision.hydrate_app_revision_context(baseline.context)
    assert baseline.context["captured_theme_config"] == (accepted if sequence == "app_revision" else recaptured)


@pytest.mark.parametrize("has_theme", [True, False])
async def test_assembly_reads_verified_theme_without_writing_lifecycle_input(baseline, has_theme):
    from mozaiksai.core.workflow.agents.factory import (
        ContextVariablesBridge,
        _workflow_tool_invocation,
    )
    from mozaiksai.core.workflow.context.authority import (
        ContextAuthorityError,
        build_context_authority_policy,
    )
    from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay

    accepted = {"colors": {"accent": {"main": "#D97706"}}}
    previous_capture = {"assets": {"logo": "unrelated.png"}}
    if has_theme:
        _replace_archive(baseline, {"brand/theme_config.json": json.dumps(accepted)})
    baseline.context.update(artifact_kind="app_bundle", captured_theme_config=previous_capture)
    definitions = yaml.safe_load((Path(__file__).resolve().parents[1] /
        "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=definitions)
    context = ContextVariablesBridge(baseline.context, authority_policy=policy)
    context._bind_run(("AppGenerator", "factory-host", "theme-revision-test"), policy)
    with _workflow_tool_invocation(context):
        overlay = StructuredOutputOverlay(context, {})
        with pytest.raises(ContextAuthorityError):
            overlay.set("captured_theme_config", accepted)
        result = await assemble_app_tasks(context_variables=overlay)

    files = {item["filename"]: item["content"] for item in result["code_files"]}
    assert context.get("captured_theme_config") == previous_capture
    expected = {"identity": {"name": "Contact Desk"}, **(accepted if has_theme else {})}
    assert json.loads(files["brand/theme_config.json"]) == expected


@pytest.mark.asyncio
async def test_scoped_assembly_hydrates_verified_archive_and_preserves_baseline(baseline):
    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "records", "route": "/records",
        "title": "Records", "page_type": "settings", "layout": "full-width",
        "sections": [{"id": "header", "primitive": "PageHeader", "config": {"title": "Records"}}],
    }
    context = create_context_container(initial={
        **baseline.context,
        "app_build_plan": {"pages": [page], "build_tasks": [{
            "task_id": "records", "task_type": "page_bundle", "owned_paths": ["ui/pages/records.yaml"],
        }]},
        "app_task_batch_results": {"records": {"manifest": None, "pages": [page]}},
    })
    result = await assemble_app_tasks(context_variables=context)
    files = {entry["filename"]: entry["content"] for entry in result["code_files"]}
    assert baseline.content.get_bundle.await_args_list == [call("immutable-archive"), call("immutable-archive")]
    assert yaml.safe_load(files["ui/pages/records.yaml"]) == page
    for path in baseline.files.keys() - {"ui/pages/records.yaml", "app.json"}:
        assert files[path] == baseline.files[path]
    expected_manifest = {**json.loads(baseline.files["app.json"]), "admins": []}
    assert json.loads(files["app.json"]) == expected_manifest
    assert json.loads(context.get("generated_files")["app.json"]) == expected_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "missing_id", "missing_artifact", "wrong_target", "wrong_owner", "wrong_registry",
    "wrong_family", "archived", "deleted", "tampered", "missing_principal",
])
async def test_bad_revision_baseline_fails_without_mutating_generated_files(baseline, fault):
    context = baseline.context
    context.update(app_plan_ready=True, app_task_batch_items=[{"task_id": "cached"}],
                   app_build_plan={"auth_mode": "public_self_signup"}, app_task_batch_status="planned")
    if fault == "missing_id":
        context["artifact_version_id"] = None
    elif fault == "missing_artifact":
        baseline.store.get_build_record.return_value = None
    elif fault == "wrong_target":
        baseline.artifact.app_id = "foreign-app"
    elif fault == "wrong_owner":
        baseline.artifact.commit_metadata.author_user_id = "foreign-owner"
    elif fault == "wrong_registry":
        baseline.artifact.commit_metadata.metadata["build_registry_id"] = "foreign-registry"
    elif fault == "wrong_family":
        baseline.artifact.build_family = "workflow_bundle"
    elif fault in {"archived", "deleted"}:
        baseline.artifact.lifecycle_status = fault
    elif fault == "tampered":
        baseline.content.get_bundle.return_value += b"tampering"
    else:
        context.pop("user_id")
    with pytest.raises(ValueError):
        await revision.hydrate_app_revision_context(context)
    assert "generated_files" not in context
    assert not context["app_plan_ready"] and not context["app_task_batch_items"]
    assert context["app_build_plan"] is None and context["app_task_batch_status"] == "planned"


@pytest.mark.asyncio
async def test_imported_genesis_requires_owner_receipt_before_revision_hydration(baseline, monkeypatch):
    baseline.artifact.commit_metadata.metadata["bundle_mode"] = "brownfield_genesis_import"
    check = AsyncMock(side_effect=GenesisImportError("no accepted review receipt"))
    monkeypatch.setattr(revision_app_source, "require_accepted_genesis_baseline", check)
    with pytest.raises(GenesisImportError, match="no accepted review receipt"):
        await revision.hydrate_app_revision_context(baseline.context)
    check.assert_awaited_once_with(
        baseline.artifact, owner_user_id="owner", execution_app_id="factory-host",
        build_registry_id="registry_1",
    )
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,sequence", [
    ("initial", "genesis"), ("revision", "conceptual_replan"), ("revision", "full_rebuild"),
])
async def test_new_or_explicit_rebuild_does_not_copy_old_implementation(baseline, mode, sequence):
    baseline.context.update(build_mode=mode, workflow_sequence=sequence)
    assert (await revision.hydrate_app_revision_context(baseline.context))["status"] == "skipped"
    baseline.store.get_build_record.assert_not_awaited()


@pytest.mark.parametrize("sequence", ["conceptual_replan", "full_rebuild"])
@pytest.mark.parametrize("pinned", [False, True])
def test_explicit_rebuild_retains_revision_parent_selection(baseline, sequence, pinned):
    baseline.context.update(
        build_mode="revision", workflow_sequence=sequence, artifact_version_id="original_app",
        revision_source_artifact_version_id="pinned_app" if pinned else None,
    )
    assert revision.revision_source_artifact_id(baseline.context) == (
        "pinned_app" if pinned else "original_app"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence", ["full_rebuild", "conceptual_replan"])
async def test_cross_stage_rebuild_reads_original_app_archive_and_keeps_new_workflow(baseline, sequence):
    context = {
        **baseline.context, "workflow_sequence": sequence, "artifact_kind": "app_bundle",
        "artifact_version_id": "new_workflow", "workflow_bundle_artifact_version_id": "new_workflow",
        "refinement_request_meta": {"build_family": "app_bundle", "build_record_id": baseline.artifact.id},
    }
    workflow = baseline.artifact.model_copy(update={
        "id": "new_workflow", "build_family": "workflow_bundle", "build_key": "new_workflow",
    })
    records = {baseline.artifact.id: baseline.artifact, workflow.id: workflow}
    baseline.store.get_build_record.side_effect = lambda **params: records[params["build_record_id"]]

    assert (await revision.hydrate_app_revision_context(context))["reason"] == "explicit_rebuild"
    assert await _verified_revision_source({}, context) == baseline.files
    baseline.store.get_build_record.assert_awaited_once_with(
        app_id="customer-app", build_record_id=baseline.artifact.id,
    )
    assert context["workflow_bundle_artifact_version_id"] == workflow.id
    assert context["artifact_version_id"] == workflow.id
    assert context["run_build_binding"]["build_id"] == "new-build"
    assert "generated_files" not in context
    context["artifact_version_id"] = "new_app_draft"
    assert revision.revision_source_artifact_id(context) == baseline.artifact.id


@pytest.mark.asyncio
async def test_rebuild_with_incomplete_original_app_request_cannot_use_workflow_as_parent(baseline):
    baseline.context.update(
        workflow_sequence="full_rebuild", artifact_version_id="new_workflow",
        refinement_request_meta={"build_family": "app_bundle"},
    )
    with pytest.raises(ValueError, match="revision_baseline_binding_missing"):
        await revision.read_bound_revision_files(baseline.context)
    baseline.store.get_build_record.assert_not_awaited()


def _upstream_design_revision(baseline, *, bundles):
    baseline.artifact.validation_status = "passed"
    baseline.artifact.commit_metadata.metadata["acceptance"] = build_acceptance_evidence(
        baseline.artifact, accepted_by="owner", accepted_at="2026-11-07T12:00:00Z",
        transition="studio_accept",
    )
    design = baseline.artifact.model_copy(deep=True, update={
        "id": "design_1", "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "files_manifest": [],
    })
    baseline.context.update(
        workflow_sequence="design_revision", artifact_kind="design_docs", artifact_version_id="design_1",
        refinement_request_meta={"build_family": "design_docs", "build_record_id": "design_1"},
    )
    records = {"design_1": design, baseline.artifact.id: baseline.artifact}
    baseline.store.get_build_record.side_effect = lambda **kwargs: records.get(kwargs["build_record_id"])
    baseline.store.list_build_records = AsyncMock(return_value=bundles)
    return design


@pytest.mark.asyncio
async def test_pre_app_design_revision_selects_first_generation_without_reading_design_as_zip(baseline):
    from factory_app.workflows.AppGenerator.tools.default_runtime_configs import app_admin_bootstrap

    _upstream_design_revision(baseline, bundles=[])
    baseline.context["platform_user_email"] = "owner@example.test"

    result = await revision.hydrate_app_revision_context(baseline.context)

    assert result == {"status": "skipped", "reason": "first_generation"}
    assert app_admin_bootstrap(baseline.context) == ["owner@example.test"]
    assert not revision.revision_baseline_required(baseline.context)
    assert revision.revision_source_artifact_id(baseline.context) is None
    assert baseline.context["artifact_version_id"] == "design_1"
    assert baseline.context["run_build_binding"]["build_id"] == "new-build"
    baseline.store.list_build_records.assert_awaited_once_with(
        app_id="customer-app", build_family="app_bundle", limit=1,
    )
    baseline.content.get_bundle.assert_not_awaited()


@pytest.mark.asyncio
async def test_design_revision_selects_and_pins_actual_owned_app_bundle(baseline):
    _upstream_design_revision(baseline, bundles=[baseline.artifact])

    result = await revision.hydrate_app_revision_context(baseline.context)

    assert result["status"] == "hydrated"
    assert revision.revision_source_artifact_id(baseline.context) == "artifact_1"
    assert baseline.context["artifact_version_id"] == "design_1"
    baseline.context["artifact_version_id"] = "workflow_or_result_2"
    baseline.store.list_build_records.return_value = [baseline.artifact.model_copy(update={"id": "newer_bundle"})]
    await revision.hydrate_app_revision_context(baseline.context)
    assert revision.revision_source_artifact_id(baseline.context) == "artifact_1"
    baseline.store.list_build_records.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("fault", ["missing", "other_record", "bad_time", "bad_transition", "missing_principal"])
async def test_design_revision_rejects_unaccepted_stale_bundle_even_when_pinned(baseline, pinned, fault):
    _upstream_design_revision(baseline, bundles=[baseline.artifact])
    if pinned:
        await revision.hydrate_app_revision_context(baseline.context)
        baseline.context.pop("generated_files")
        baseline.content.get_bundle.reset_mock()
    metadata = baseline.artifact.commit_metadata.metadata
    if fault == "missing":
        metadata.pop("acceptance")  # Invalidation also makes an unaccepted draft stale.
    elif fault == "other_record":
        metadata["acceptance"]["build_record_id"] = "other"
    elif fault == "bad_time":
        metadata["acceptance"]["accepted_at"] = "not-a-time"
    elif fault == "bad_transition":
        metadata["acceptance"]["transition"] = "generated"
    else:
        metadata["acceptance"]["accepted_by"] = " "
    with pytest.raises(ValueError, match="revision_baseline_not_accepted"):
        await revision.hydrate_app_revision_context(baseline.context)
    baseline.content.get_bundle.assert_not_awaited()
    assert "generated_files" not in baseline.context
    assert baseline.context["revision_first_generation"] is False


@pytest.mark.asyncio
async def test_design_revision_current_bundle_retains_lifecycle_acceptance(baseline):
    _upstream_design_revision(baseline, bundles=[baseline.artifact])
    baseline.artifact.lifecycle_status = "current"
    baseline.artifact.commit_metadata.metadata.pop("acceptance")
    assert (await revision.hydrate_app_revision_context(baseline.context))["status"] == "hydrated"


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True])
async def test_design_revision_imported_stale_bundle_uses_canonical_receipt(baseline, monkeypatch, accepted):
    _upstream_design_revision(baseline, bundles=[baseline.artifact])
    baseline.artifact.commit_metadata.metadata.pop("acceptance")
    baseline.artifact.commit_metadata.metadata["bundle_mode"] = "brownfield_genesis_import"
    check = AsyncMock(side_effect=None if accepted else GenesisImportError("missing receipt"))
    monkeypatch.setattr(revision_app_source, "require_accepted_genesis_baseline", check)
    if accepted:
        assert (await revision.hydrate_app_revision_context(baseline.context))["status"] == "hydrated"
    else:
        with pytest.raises(GenesisImportError, match="missing receipt"):
            await revision.hydrate_app_revision_context(baseline.context)
        baseline.content.get_bundle.assert_not_awaited()
        assert "generated_files" not in baseline.context
    check.assert_awaited_once_with(
        baseline.artifact, owner_user_id="owner", execution_app_id="factory-host",
        build_registry_id="registry_1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "missing_design", "foreign_design_owner", "foreign_design_registry", "wrong_design_family",
    "wrong_design_workflow", "retired_design", "missing_request", "wrong_phase",
    "lookup_error", "invalid_lookup", "foreign_bundle", "foreign_bundle_owner",
    "foreign_bundle_registry", "wrong_bundle_key", "draft_bundle", "failed_bundle",
    "pending_bundle", "retired_bundle", "missing_pinned_bundle", "tampered_bundle",
])
async def test_design_revision_cannot_turn_bad_or_existing_baseline_into_first_generation(baseline, fault):
    design = _upstream_design_revision(baseline, bundles=[baseline.artifact])
    context = baseline.context
    context["revision_first_generation"] = True  # A carried/replayed result is not proof.
    if fault == "missing_design":
        baseline.store.get_build_record.side_effect = None
        baseline.store.get_build_record.return_value = None
    elif fault == "foreign_design_owner":
        design.commit_metadata.author_user_id = "other"
    elif fault == "foreign_design_registry":
        design.commit_metadata.metadata["build_registry_id"] = "other"
    elif fault == "wrong_design_family":
        design.build_family = "concept"
    elif fault == "wrong_design_workflow":
        design.source_workflow = "ValueEngine"
    elif fault == "retired_design":
        design.lifecycle_status = "deleted"
    elif fault == "missing_request":
        context.pop("refinement_request_meta")
    elif fault == "wrong_phase":
        context["run_build_binding"]["phase"] = "genesis"
    elif fault == "lookup_error":
        baseline.store.list_build_records.side_effect = RuntimeError("storage unavailable")
    elif fault == "invalid_lookup":
        baseline.store.list_build_records.return_value = None
    elif fault == "foreign_bundle":
        baseline.artifact.app_id = "other"
    elif fault == "foreign_bundle_owner":
        baseline.artifact.commit_metadata.author_user_id = "other"
    elif fault == "foreign_bundle_registry":
        baseline.artifact.commit_metadata.metadata["build_registry_id"] = "other"
    elif fault == "wrong_bundle_key":
        baseline.artifact.build_key = "other"
    elif fault == "draft_bundle":
        baseline.artifact.lifecycle_status = "draft"
    elif fault in {"failed_bundle", "pending_bundle"}:
        baseline.artifact.validation_status = fault.split("_")[0]
    elif fault == "retired_bundle":
        baseline.artifact.lifecycle_status = "archived"
    elif fault == "missing_pinned_bundle":
        context["revision_source_artifact_version_id"] = "missing"
    else:
        baseline.content.get_bundle.return_value += b"tampered"

    with pytest.raises((ValueError, RuntimeError)):
        await revision.hydrate_app_revision_context(context)
    assert context["revision_first_generation"] is False
    assert revision.revision_baseline_required(context)
    assert "generated_files" not in context
    if fault != "missing_pinned_bundle":
        assert "revision_source_artifact_version_id" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence", ["app_revision", "feature_revision", "unknown_revision"])
async def test_carried_first_generation_flag_does_not_exempt_other_revisions(baseline, sequence):
    from factory_app.workflows.AppGenerator.tools.default_runtime_configs import app_admin_bootstrap

    baseline.context.update(workflow_sequence=sequence, revision_first_generation=True, platform_user_email="editor@example.test")
    grants = {"admins": ["owner@example.test"]}
    assert app_admin_bootstrap(baseline.context, baseline_manifest=grants) == grants["admins"]
    assert revision.revision_baseline_required(baseline.context)
    await revision.hydrate_app_revision_context(baseline.context)
    assert baseline.context["revision_first_generation"] is False
    assert app_admin_bootstrap(baseline.context, baseline_manifest=grants) == grants["admins"]
    baseline.content.get_bundle.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_generation_rechecks_empty_family_on_resume_and_preserves_build_inputs(baseline):
    _upstream_design_revision(baseline, bundles=[])
    context = create_context_container(initial={
        **baseline.context,
        "design_docs_build_artifact": {"id": "new-design", "review": {"build_id": "new-build"}},
    })
    before = context.snapshot()
    await revision.hydrate_app_revision_context(context)
    assert all(context.get(key) == value for key, value in before.items())
    baseline.store.list_build_records.side_effect = RuntimeError("storage unavailable")
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await revision.hydrate_app_revision_context(context)
    assert context.get("revision_first_generation") is False
    assert revision.revision_baseline_required(context)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_bundle", [False, True])
async def test_design_revision_actual_assembly_selects_only_implementation_baseline(baseline, has_bundle):
    _upstream_design_revision(baseline, bundles=[baseline.artifact] if has_bundle else [])
    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "records", "route": "/records",
        "title": "Records", "page_type": "settings", "layout": "full-width",
        "sections": [{"id": "header", "primitive": "PageHeader", "config": {"title": "Records"}}],
    }
    context = create_context_container(initial={
        **baseline.context,
        "app_build_plan": {"pages": [page], "build_tasks": [{
            "task_id": "records", "task_type": "page_bundle", "owned_paths": ["ui/pages/records.yaml"],
        }]},
        "app_task_batch_results": {"records": {"manifest": None, "pages": [page]}},
    })
    result = await assemble_app_tasks(context_variables=context)
    assert result.get("status") != "failed", result
    files = {entry["filename"]: entry["content"] for entry in result["code_files"]}
    assert yaml.safe_load(files["ui/pages/records.yaml"]) == page
    assert ("modules/records/backend/handler.py" in files) is has_bundle
    assert context.get("run_build_binding")["build_id"] == "new-build"
    assert context.get("artifact_version_id") == "design_1"


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence", ["app_revision", "design_revision"])
@pytest.mark.parametrize("old_timestamp", ["absent", "optional", "required"])
async def test_revision_hydrates_only_previously_unrequired_read_timestamps(scoped_module_revision, sequence, old_timestamp):
    from copy import deepcopy

    source = scoped_module_revision.baseline
    stored = json.loads(source.files["data/contract.json"])
    current = deepcopy(stored)
    timestamp = {"name": "updated_at", "type": "datetime", "required": True}
    current["surfaces"][0]["collections"][0]["fields"].append(timestamp)
    if old_timestamp != "absent":
        stored["surfaces"][0]["collections"][0]["fields"].append(timestamp)
    stored["surfaces"][0]["collections"].append({"name": "older_partial_metadata"})
    record = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    if old_timestamp != "absent":
        record["properties"]["updated_at"] = {}
    if old_timestamp == "required":
        record["required"].append("updated_at")
    get = {"id": "get_records", "handler_method": "get_records",
           "output_schema": {"type": "object", "properties": {"item": record}}}
    listing = {"id": "list_records", "handler_method": "list_records", "output_schema": {"type": "object", "properties": {
        "items": {"type": "array", "items": {**deepcopy(record), "required": ["id", "updated_at"]}}}}}
    path = "modules/records/module.yaml"
    manifest = yaml.safe_dump({"module": {"id": "records"}, "actions": [get, listing]})
    _replace_archive(source, {path: manifest, "data/contract.json": json.dumps(stored)})
    if sequence == "design_revision":
        _upstream_design_revision(source, bundles=[source.artifact])
    else:
        source.artifact.validation_status = "passed"
        source.artifact.commit_metadata.metadata["acceptance"] = build_acceptance_evidence(
            source.artifact, accepted_by="owner", accepted_at="2026-11-07T12:00:00Z", transition="studio_accept")
    context = {**source.context, "data_contract": current, "generated_files": {path: "actions: []"}}
    await revision.hydrate_app_revision_context(context)
    expected = {} if old_timestamp == "required" else {"records": ["get_records"]}
    assert context["legacy_optional_updated_at_reads"] == expected
    assert context["revision_source_artifact_version_id"] == source.artifact.id
    assert context["generated_files"][path] == "actions: []"
    if sequence == "app_revision":
        source.artifact.commit_metadata.metadata.pop("acceptance")
        await revision.hydrate_app_revision_context(context)
        assert context["legacy_optional_updated_at_reads"] == {}


def test_old_read_timestamp_projection_requires_matching_record_and_action(scoped_module_revision):
    from copy import deepcopy

    from mozaiksai.core.workflow.generator_support.module_revision import (
        legacy_optional_updated_at_reads,
    )

    base = scoped_module_revision.baseline
    stored = json.loads(base.files["data/contract.json"])
    current = deepcopy(stored)
    collection = current["surfaces"][0]["collections"][0]
    collection["fields"].append({"name": "updated_at", "type": "datetime", "required": True})
    path = "modules/records/module.yaml"
    record = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    good = {"id": "list_records", "handler_method": "list_records", "output_schema": {"type": "object",
            "properties": {"items": {"type": "array", "items": record}}}}
    compound = {**deepcopy(good), "output_schema": {**deepcopy(good["output_schema"]), "allOf": [{"required": ["updated_at"]}]}}
    for candidate in ({**good, "id": "custom"}, {**good, "handler_method": "custom"}, compound, {},
                      {**good, "output_schema": {"type": "object", "properties": {"items": True}}},
                      {**good, "output_schema": {"type": "object", "properties": {"items": {"items": record}}}}):
        files = {**base.files, path: yaml.safe_dump({"module": {"id": "records"}, "actions": [candidate]})}
        assert legacy_optional_updated_at_reads(files, current) == {}
    files = {**base.files, path: yaml.safe_dump({"module": {"id": "records"}, "actions": [good]})}
    assert legacy_optional_updated_at_reads(files, current) == {"records": ["list_records"]}
    collection["fields"].append({"name": "record_id", "type": "string", "required": True})
    assert legacy_optional_updated_at_reads(files, current) == {}
    collection["fields"].pop()
    collection["entity"] = "Different"
    assert legacy_optional_updated_at_reads(files, current) == {}


def test_first_generation_state_has_closed_authority_and_reaches_generation_agents():
    from mozaiksai.core.workflow.context.authority import (
        ContextAuthorityError,
        build_context_authority_policy,
    )

    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"))
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=config["definitions"])
    for key in ("revision_first_generation", "revision_source_artifact_version_id", "legacy_optional_updated_at_reads"):
        for writer in ("caller_input", "transition_router", "structured_output", "context_bridge", "agent_text"):
            with pytest.raises(ContextAuthorityError):
                policy.require_can_write(key, writer_id=writer)
        for writer in ("lifecycle_tool", "deterministic_tool"):
            policy.require_can_write(key, writer_id=writer)
    assert config["definitions"]["revision_first_generation"]["source"]["default"] is False
    assert config["definitions"]["legacy_optional_updated_at_reads"]["model_visible"] is False
    for agent in ("InterviewAgent", "AppPlanAgent", "DatabaseAgent"):
        assert {"revision_first_generation", "workflow_sequence"} <= set(config["agents"][agent]["variables"])


@pytest.mark.asyncio
async def test_first_generation_requires_complete_approved_plan(baseline):
    from factory_app.workflows.AppGenerator.tools.app_plan_review import validate_plan_coverage

    _upstream_design_revision(baseline, bundles=[])
    await revision.hydrate_app_revision_context(baseline.context)
    baseline.context["experience_spec"] = {"pages": [{"name": "Records", "route": "/records", "ui_surface": "declarative_page"}]}
    incomplete = {"pages": [], "build_tasks": [{"task_id": "partial", "task_type": "page_bundle", "owned_paths": []}]}
    with pytest.raises(ValueError, match="pages must preserve the approved name/route inventory"):
        validate_plan_coverage(incomplete, baseline.context)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_bundle", [False, True])
async def test_baseline_loader_uses_declared_lifecycle_context_authority(baseline, has_bundle):
    from mozaiksai.core.workflow.context.authority import build_context_authority_policy

    _upstream_design_revision(baseline, bundles=[baseline.artifact] if has_bundle else [])
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"))
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=config["definitions"])
    context = create_context_container(initial=baseline.context, authority_policy=policy, writer_id="lifecycle_tool")
    await revision.hydrate_app_revision_context(context)
    assert context.get("revision_first_generation") is not has_bundle
    assert revision.revision_source_artifact_id(context) == ("artifact_1" if has_bundle else None)


@pytest.mark.parametrize(("fault", "expected"), [
    ("none", True), ("auth_scope", False), ("missing_scope", False), ("public_source", False),
    ("registration_enabled", False), ("prior_mode_drift", False), ("unbound", False),
])
async def test_feature_auth_projection_uses_verified_compilation_and_retained_files(baseline, monkeypatch, fault, expected):
    document = yaml.safe_load((Path(__file__).resolve().parents[1] / "factory_app/app/config/auth.yaml").read_text())
    document.update(mode="brokered_oidc", signup_enabled=fault == "registration_enabled", authorization={"roles": []})
    _replace_archive(baseline, {
        "app.json": json.dumps({"appId": "customer-app", "authRequired": fault != "public_source"}),
        "config/auth.yaml": yaml.safe_dump(document),
    })
    _upstream_design_revision(baseline, bundles=[baseline.artifact])
    baseline.artifact.commit_metadata.metadata["compilation_inputs"] = {"version": 1 if fault == "unbound" else 2}
    prior_mode = "public_self_signup" if fault == "prior_mode_drift" else "brokered_oidc"
    resolver = AsyncMock(return_value=SimpleNamespace(context_for=lambda app_id: {
        "app_build_plan": {"auth_strategy": "basic-login", "auth_mode": prior_mode, "roles": []},
    }))
    monkeypatch.setattr(revision, "resolve_app_compilation_inputs", resolver)
    paths = {"auth_scope": ["config/auth.yaml"], "missing_scope": []}.get(fault, ["modules/records/module.yaml"])
    baseline.context.update(revision_scope="feature", impact_set={"affected_bundle_paths": paths},
                            generated_files={"config/auth.yaml": "untrusted: true"})
    await revision.hydrate_app_revision_context(baseline.context)
    assert baseline.context["retained_feature_login"] == ({
        "source_artifact_version_id": baseline.artifact.id, "auth_strategy": "basic-login", "auth_mode": "brokered_oidc",
    } if expected else {})
    if expected:
        resolver.assert_awaited_once_with(app_id="customer-app", build_record_id=baseline.artifact.id,
                                         owner_user_id="owner", build_registry_id="registry_1")
        # Assembly also re-verifies the source before using a previously hydrated plan.
        baseline.context["retained_feature_login"] = {"auth_mode": "public_self_signup"}
        await revision.hydrate_revision_files(baseline.context)
        assert baseline.context["retained_feature_login"]["auth_mode"] == "brokered_oidc"
        assert resolver.await_count == 2


def test_launch_context_cannot_inject_first_generation_or_baseline_selection(monkeypatch):
    from mozaiksai.core.session.launcher import validate_context_for_workflow
    from mozaiksai.core.workflow.context.authority import ContextAuthorityError
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"))
    monkeypatch.setattr(workflow_manager, "get_config", lambda name: {"context_variables": config})
    for writer in ("caller_input", "transition_router"):
        for seed in ({"revision_first_generation": True}, {"revision_source_artifact_version_id": "foreign"},
                     {"retained_feature_login": {"auth_strategy": "basic-login"}}):
            with pytest.raises(ContextAuthorityError):
                validate_context_for_workflow("AppGenerator", seed, writer_id=writer)


@pytest.mark.asyncio
async def test_router_seed_survives_upstream_result_id_changes_before_first_generation(baseline, monkeypatch):
    from mozaiksai.control_plane.implementations.refinement_router import (
        ChangeIntent,
        ImpactSet,
        RefinementRequest,
        RefinementTriggerRouteResolver,
    )

    _upstream_design_revision(baseline, bundles=[])
    router = RefinementTriggerRouteResolver()
    monkeypatch.setattr(router, "_current_app_context_seed", AsyncMock(return_value={}))
    request = RefinementRequest(
        app_id="factory-host", target_app_id="customer-app", user_id="owner",
        build_family="design_docs", build_key="design_docs", build_record_id="design_1",
        raw_user_request="Correct the approved page contract and rebuild downstream.",
        requested_workflow_id="DesignDocs",
    )
    seed = await router._build_context_seed(
        request=request, change_intent=ChangeIntent(change_class="design", rationale="Design correction"),
        impact_set=ImpactSet(workflow_sequence="design_revision"),
    )
    context = create_context_container(initial={**baseline.context, **seed})
    # AgentGenerator replaces its result pointer without changing the saved request.
    context.set("artifact_version_id", "new-workflow-bundle")
    await revision.hydrate_app_revision_context(context)
    assert context.get("revision_first_generation") is True
    assert context.get("artifact_version_id") == "new-workflow-bundle"
    assert context.get("refinement_request_meta")["build_record_id"] == "design_1"
    assert context.get("run_build_binding")["build_id"] == "new-build"


@pytest.mark.asyncio
@pytest.mark.parametrize("has_bundle", [False, True])
async def test_design_revision_registration_uses_app_lineage_not_design_parent(baseline, monkeypatch, tmp_path, has_bundle):
    from tests.factory_export_fixture import draft_inputs

    _upstream_design_revision(baseline, bundles=[baseline.artifact] if has_bundle else [])
    records, inputs = draft_inputs(app="customer-app", build="new-build")
    context = create_context_container(initial={**inputs, **baseline.context})
    await revision.hydrate_app_revision_context(context)
    context.set("app_bundle_acceptance_status", "passed")
    raw = baseline.content.get_bundle.return_value
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(raw)
    recorded = []

    async def create_record(**kwargs):
        recorded.append(kwargs)
        return BuildRecord(
            id="new-app-bundle", app_id=kwargs["app_id"], build_family=kwargs["build_family"],
            build_key=kwargs["build_key"], version_number=2 if has_bundle else 1,
            lineage_root_id="artifact_1" if has_bundle else "new-app-bundle",
            parent_build_record_id=kwargs["parent_build_record_id"],
            files_manifest=kwargs["files_manifest"], commit_metadata=kwargs["commit_metadata"],
        )

    monkeypatch.setattr(content_store, "get_artifact_content_store", lambda: SimpleNamespace(backend_name="local"))
    monkeypatch.setattr(generate_and_download, "_register_greenfield_app_context_for_bundle", AsyncMock())
    await generate_and_download._register_app_bundle_artifact_version(
        app_id="customer-app", user_id="owner", workflow_name="AppGenerator", chat_id="new-chat",
        bundle_name="bundle", zip_path=archive, context_variables=context,
        artifact_store=SimpleNamespace(create_build_record=create_record, list_build_records=AsyncMock(return_value=[]),
                                       get_build_record=AsyncMock(side_effect=lambda *, app_id, build_record_id: records[build_record_id])),
    )
    assert recorded[0]["parent_build_record_id"] == ("artifact_1" if has_bundle else None)
    assert recorded[0]["canonical_inputs_version"] == {"design_docs": "draft-design_docs", "subscription_contract": "draft-subscription_contract"}
    assert recorded[0]["commit_metadata"]["metadata"]["build_id"] == "new-build"
    assert recorded[0]["commit_metadata"]["metadata"]["phase"] == "refinement"


@pytest.mark.asyncio
async def test_revision_hydrates_large_text_and_keeps_binary_out_of_agent_context(baseline):
    large_svg = "<svg>" + "x" * 2_550_000 + "</svg>"
    _replace_archive(baseline, {
        "brand/favicon.svg": large_svg,
        "brand/logo.png": b"\x89PNG" + bytes(2_100_000),
        "brand/font.otf": b"OTTO" + bytes(300),
    })

    result = await revision.hydrate_app_revision_context(baseline.context)

    assert result["status"] == "hydrated"
    assert "brand/favicon.svg" not in baseline.context["generated_files"]
    assert "brand/logo.png" not in baseline.context["generated_files"]
    assert "brand/font.otf" not in baseline.context["generated_files"]
    assert (await revision.read_bound_revision_binary_assets(baseline.context)) == {
        "brand/favicon.svg": large_svg.encode("utf-8"),
        "brand/logo.png": b"\x89PNG" + bytes(2_100_000),
        "brand/font.otf": b"OTTO" + bytes(300),
    }
    security_files, _ = await read_artifact_bundle(baseline.artifact, retain_svg_text=True)
    assert security_files["brand/favicon.svg"] == large_svg
    assert "brand/logo.png" not in security_files


@pytest.mark.asyncio
async def test_revision_rejects_archive_entries_outside_its_canonical_root(baseline):
    _replace_archive(baseline, {"other/foreign.py": "VALUE = 1\n"})

    with pytest.raises(ValueError, match="outside_bundle_root"):
        await revision.hydrate_app_revision_context(baseline.context)
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
@pytest.mark.parametrize("path,reason", [
    ("brand/broken.svg", "non_text_source"),
    ("brand/too-large.png", "file_too_large"),
])
async def test_revision_rejects_malformed_or_oversized_opaque_assets(baseline, path, reason):
    content = b"\xff" if path.endswith(".svg") else bytes(8_000_001)
    _replace_archive(baseline, {path: content})

    with pytest.raises(ValueError, match=reason):
        await revision.hydrate_app_revision_context(baseline.context)
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "brand/x:payload.png", "brand/CON.png", "brand/COM¹.png", "brand/LPT².png",
])
async def test_revision_rejects_nonportable_opaque_asset_name(baseline, path):
    _replace_archive(baseline, {path: b"\x89PNG"})

    with pytest.raises(ValueError, match="unsafe_path"):
        await revision.hydrate_app_revision_context(baseline.context)
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
async def test_revision_rejects_casefold_colliding_opaque_asset_names(baseline):
    _replace_archive(baseline, {
        "brand/Logo.png": b"first", "brand/logo.png": b"second",
    })

    with pytest.raises(ValueError, match="duplicate_path"):
        await revision.hydrate_app_revision_context(baseline.context)
    assert "generated_files" not in baseline.context


def _download_context(baseline, *, deleted=(), replacement=None):
    files = dict(baseline.files)
    if replacement is not None:
        files["brand/logo.png"] = replacement
    return create_context_container(initial={
        **baseline.context,
        "chat_id": "revision-chat", "app_name": "RevisedApp",
        "generated_files": files,
        "deleted_files": list(deleted),
    })


def _stub_download_boundaries(monkeypatch, tmp_path):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download, "_inject_agent_context_env", AsyncMock())
    async def accept_source(*, files, context_variables, **_kwargs):
        try:
            _, evidence = await revision.revision_asset_evidence(context_variables, files)
        except (OSError, ValueError) as exc:
            return {"passed": False, "status": "failed", "bundle_scan": {"errors": [str(exc)]}}
        context_variables.set("revision_source_artifact_version_id", evidence["source_artifact_version_id"])
        context_variables.set("revision_asset_evidence", evidence)
        context_variables.set("generated_files", files)
        context_variables.set("app_build_plan", {"build_tasks": []})
        context_variables.set("app_validation_status", "passed")
        context_variables.set("integration_tests_passed", True)
        context_variables.set("app_bundle_acceptance_status", "passed")
        context_variables.set("app_bundle_acceptance_result", {
            "snapshot_digest": artifact_snapshot_digest(context_variables, files),
        })
        return {"passed": True, "status": "passed", "bundle_scan": {"errors": []}}

    monkeypatch.setattr(generate_and_download, "run_app_bundle_acceptance_gate", accept_source)
    monkeypatch.setattr(generate_and_download, "resolve_export_gate", lambda *_args, **_kwargs: {
        "allow_export": True, "reasons": [],
    })
    register = generate_and_download._register_app_bundle_artifact_version
    monkeypatch.setattr(generate_and_download, "_register_app_bundle_artifact_version", AsyncMock(return_value=None))
    registry = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(generate_and_download.AppRegistryService, "update_build_status", registry)
    ui = AsyncMock(return_value={"status": "completed", "action": "continue"})
    monkeypatch.setattr(generate_and_download, "use_ui_tool", ui)
    selection = AsyncMock(return_value=None)
    monkeypatch.setattr(generate_and_download, "_check_workbench_selection", selection)
    return SimpleNamespace(registry=registry, ui=ui, register=register, selection=selection)


@pytest.mark.asyncio
async def test_revision_final_zip_preserves_exact_binary_and_canonical_manifest(baseline, monkeypatch, tmp_path):
    binary = b"\x89PNG" + bytes(2_100_000)
    svg = b"<svg>" + b"x" * 2_550_000 + b"</svg>"
    _replace_archive(baseline, {"brand/logo.png": binary, "brand/favicon.svg": svg})
    context = _download_context(baseline)
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert result["status"] == "success", result
    archive_path = Path(result["bundle_zip"])
    with zipfile.ZipFile(archive_path) as archive:
        entries = set(archive.namelist())
        assert "RevisedApp/app.json" in entries
        assert archive.read("RevisedApp/brand/favicon.svg") == svg
        assert archive.read("RevisedApp/brand/logo.png") == binary
    manifest = _build_generated_files_manifest(
        bundle_name="RevisedApp", app_dir=Path(result["bundle_dir"]), written_paths=result["files_written"],
    )
    binary_manifest = [entry for entry in manifest if entry["path"] == "RevisedApp/brand/logo.png"]
    assert binary_manifest == [{
        "path": "RevisedApp/brand/logo.png", "sha256": hashlib.sha256(binary).hexdigest(),
        "size_bytes": len(binary), "content_type": "application/octet-stream",
    }]
    recorded = []

    async def create_record(**kwargs):
        recorded.append(kwargs)
        return BuildRecord(
            id="revised-record", app_id=kwargs["app_id"], build_family=kwargs["build_family"],
            build_key=kwargs["build_key"], version_number=2, lineage_root_id="artifact_1",
            files_manifest=kwargs["files_manifest"], commit_metadata=kwargs["commit_metadata"],
        )

    artifacts = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts, "resolve_latest_artifact_version_refs", AsyncMock(return_value={}))
    monkeypatch.setattr(generate_and_download, "_register_greenfield_app_context_for_bundle", AsyncMock())
    record = await boundaries.register(
        app_id="customer-app", user_id="owner", workflow_name="AppGenerator", chat_id="revision-chat",
        bundle_name="RevisedApp", zip_path=archive_path, app_dir=Path(result["bundle_dir"]),
        written_paths=result["files_written"], context_variables=context,
        artifact_store=SimpleNamespace(create_build_record=create_record),
    )
    canonical = resolve_canonical_bundle_entry(record)
    assert canonical.path == "RevisedApp/RevisedApp.zip"
    assert canonical.sha256 == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert recorded[0]["parent_build_record_id"] == "artifact_1"
    assert [entry.model_dump() for entry in record.files_manifest[1:]] == manifest
    boundaries.registry.assert_awaited_once()
    boundaries.ui.assert_awaited_once()
    assert boundaries.selection.await_args_list == [call(
        context, app_id="factory-host", user_id="owner", artifact_version_id=None,
        response={"status": "completed", "action": "continue"},
    )] * 2


@pytest.mark.asyncio
async def test_revision_opaque_deletion_fails_acceptance_before_zip(baseline, monkeypatch, tmp_path):
    _replace_archive(baseline, {"brand/logo.png": b"\x89PNG"})
    context = _download_context(baseline, deleted=["brand/logo.png"])
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert result["status"] == "error" and result["outcome"] == "blocked"
    assert "revision_binary_deletion_unowned" in result["bundle_errors"][0]
    assert not list(tmp_path.rglob("*.zip"))
    boundaries.registry.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["late_tombstone", "changed_source_bytes"])
async def test_revision_rejects_source_changes_after_acceptance_before_zip(
    baseline, monkeypatch, tmp_path, mutation,
):
    _replace_archive(baseline, {"brand/logo.png": b"\x89PNG-original"})
    context = _download_context(baseline)
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)
    accepted = generate_and_download.run_app_bundle_acceptance_gate

    async def mutate_after_acceptance(**kwargs):
        result = await accepted(**kwargs)
        if mutation == "late_tombstone":
            context.set("deleted_files", ["brand/logo.png"])
        else:
            _replace_archive(baseline, {"brand/logo.png": b"\x89PNG-changed"})
        return result

    monkeypatch.setattr(generate_and_download, "run_app_bundle_acceptance_gate", mutate_after_acceptance)
    monkeypatch.setattr(generate_and_download, "resolve_export_gate", export_app_code.resolve_export_gate)

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert result["status"] == "error" and result["outcome"] == "blocked"
    assert not list(tmp_path.rglob("*.zip"))
    boundaries.registry.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_workflow", [False, True])
async def test_revision_acceptance_binds_source_hashes_and_tombstones(baseline, monkeypatch, with_workflow):
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )
    from tests.test_app_validation_strategy import _accept_support_tasks, _Context

    integration = default_workflow_integration()
    workflow = BuildRecord(
        id="reviewed-workflows", app_id="customer-app", build_family="workflow_bundle",
        build_key="workflow_bundle", lineage_root_id="reviewed-workflows", version_number=1,
        commit_metadata={"metadata": {"workflow_integration_metadata": {"workflows": [integration]}}},
    )
    records = {baseline.artifact.id: baseline.artifact}
    if with_workflow:
        baseline.artifact.canonical_inputs_version["workflow_bundle"] = workflow.id
        records[workflow.id] = workflow
    baseline.store.get_build_record.side_effect = lambda *, app_id, build_record_id: records.get(build_record_id)
    files = build_appgenerator_acceptance_files(integration)
    manifest = json.loads(files["app.json"])
    manifest["appId"] = "customer-app"
    files["app.json"] = json.dumps(manifest)
    baseline.files = files
    binary = b"\x89PNG" + bytes(11)
    _replace_archive(baseline, {"brand/logo.png": binary})
    context = _Context({**baseline.context, "generated_files": files, "deleted_files": []})
    context.set("generated_workflow_name", integration["workflow_name"])
    context.set("generated_workflow_capability_id", integration["capability_id"])
    context.set("generated_workflow_startup_mode", integration["startup_mode"])
    context.set("generated_workflow_trigger_events", integration["trigger_events"])
    context.set("app_validation_status", "passed")
    context.set("app_validation_strategy_used", "docker")
    _accept_support_tasks(context, files)
    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_app_runtime_smoke", AsyncMock(return_value={
        "status": "passed", "passed": True, "failed_tests": [], "checks": [],
    }))

    accepted = await app_validation.run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert accepted["passed"] is True, accepted
    assert context.get("workflow_integration_source_app") == {
        "build_record_id": "artifact_1", "workflow_bundle_id": workflow.id if with_workflow else None,
    }
    if with_workflow:
        assert accepted["workflow_integration"]["metadata"]["source_artifact_version_id"] == workflow.id
    else:
        assert accepted["workflow_integration"]["workflow_count"] == 0
    assert context.get("revision_asset_evidence") == {
        "source_artifact_version_id": "artifact_1",
        "opaque_assets": [{
            "path": "brand/logo.png", "sha256": hashlib.sha256(binary).hexdigest(), "size_bytes": len(binary),
        }],
        "deleted_files": [],
    }
    assert export_app_code.resolve_export_gate(context)["allow_export"] is True

    context.set("artifact_version_id", "artifact_2")
    reentered = await app_validation.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert reentered["passed"] is True, reentered
    assert export_app_code.resolve_export_gate(context)["allow_export"] is True
    assert {call.kwargs["build_record_id"] for call in baseline.store.get_build_record.await_args_list} == set(records)

    context.set("deleted_files", ["modules/support_tickets/backend/handler.py"])
    assert export_app_code.resolve_export_gate(context)["allow_export"] is False
    context.set("deleted_files", [])
    evidence = context.get("revision_asset_evidence")
    evidence["opaque_assets"][0]["sha256"] = "0" * 64
    assert export_app_code.resolve_export_gate(context)["allow_export"] is False

    context.set("deleted_files", ["brand/logo.png"])
    denied = await app_validation.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert denied["passed"] is False
    assert any("revision_binary_deletion_unowned" in error for error in denied["bundle_scan"]["errors"])

    if with_workflow:
        context.set("deleted_files", [])
        del records[workflow.id]
        unreadable = await app_validation.run_app_bundle_acceptance_gate(files=files, context_variables=context)
        assert unreadable["passed"] is False
        assert unreadable["workflow_integration"]["failed_tests"][0]["test"] == "workflow_integration_source"


@pytest.mark.asyncio
async def test_revision_source_survives_register_then_export(baseline, monkeypatch, tmp_path):
    binary = b"\x89PNG" + bytes(23)
    _replace_archive(baseline, {"brand/logo.png": binary})
    context = _download_context(baseline)
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_and_download, "_register_app_bundle_artifact_version", boundaries.register)
    monkeypatch.setattr(generate_and_download, "resolve_export_gate", export_app_code.resolve_export_gate)
    monkeypatch.setattr(generate_and_download, "_register_greenfield_app_context_for_bundle", AsyncMock())
    boundaries.ui.return_value = {"status": "completed", "action": "export_to_github"}

    async def create_record(**kwargs):
        assert kwargs["parent_build_record_id"] == "artifact_1"
        return BuildRecord(
            id="artifact_2", app_id=kwargs["app_id"], build_family=kwargs["build_family"],
            build_key=kwargs["build_key"], version_number=2, lineage_root_id="artifact_1",
            files_manifest=kwargs["files_manifest"], commit_metadata=kwargs["commit_metadata"],
        )

    baseline.store.create_build_record = create_record
    artifacts = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: baseline.store)
    monkeypatch.setattr(artifacts, "resolve_latest_artifact_version_refs", AsyncMock(return_value={}))
    monkeypatch.setattr(export_app_code, "get_latest_workflow_export", AsyncMock(return_value=None))
    provider = AsyncMock(return_value=SimpleNamespace(success=False, model_dump=lambda: {"success": False}))
    monkeypatch.setattr(export_app_code.export_to_github_tool, "execute", provider)

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert context.get("artifact_version_id") == "artifact_2"
    assert context.get("revision_source_artifact_version_id") == "artifact_1"
    assert export_app_code.resolve_export_gate(context)["allow_export"] is True
    assert result["deployment"].get("blocked") is not True, result
    provider.assert_awaited_once()
    assert boundaries.selection.await_args_list == [call(
        context, app_id="factory-host", user_id="owner", artifact_version_id="artifact_2",
        response={"status": "completed", "action": "export_to_github"},
    )] * 2
    assert baseline.store.get_build_record.await_count >= 3
    assert all(call.kwargs["build_record_id"] == "artifact_1" for call in baseline.store.get_build_record.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["text_replacement", "tampered_source", "wrong_owner"])
async def test_revision_finalization_fails_before_writing_on_invalid_binary_source(
    baseline, monkeypatch, tmp_path, fault,
):
    _replace_archive(baseline, {"brand/logo.png": b"\x89PNG"})
    context = _download_context(baseline, replacement="forbidden" if fault == "text_replacement" else None)
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)
    if fault == "tampered_source":
        baseline.content.get_bundle.return_value += b"tampered"
    elif fault == "wrong_owner":
        baseline.artifact.commit_metadata.author_user_id = "someone-else"

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert result["status"] == "error" and result["outcome"] == "blocked"
    assert not list(tmp_path.rglob("*.zip"))
    boundaries.registry.assert_not_awaited()
    boundaries.ui.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "changed_binary", "missing_binary", "extra_file"])
async def test_export_verifies_revision_binary_against_bound_source_before_provider_call(
    baseline, monkeypatch, tmp_path, fault,
):
    binary = b"\x89PNG" + bytes(10)
    _replace_archive(baseline, {"brand/logo.png": binary})
    context = _download_context(baseline)
    _, evidence = await revision.revision_asset_evidence(context, baseline.files)
    context.set("revision_source_artifact_version_id", evidence["source_artifact_version_id"])
    context.set("revision_asset_evidence", evidence)
    archive_path = tmp_path / "RevisedApp.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path, content in baseline.files.items():
            archive.writestr("RevisedApp/" + path, content)
        if fault != "missing_binary":
            archive.writestr("RevisedApp/brand/logo.png", b"different" if fault == "changed_binary" else binary)
        if fault == "extra_file":
            archive.writestr("RevisedApp/unapproved.txt", "extra")
    monkeypatch.setattr(export_app_code, "resolve_export_gate", lambda *_args, files=None, **_kwargs: {
        "allow_export": files is None or files == baseline.files,
        "reasons": [] if files is None or files == baseline.files else ["text mismatch"],
        "app_bundle_acceptance_status": "passed", "app_validation_status": "passed",
        "app_validation_strategy_used": "docker", "integration_tests_passed": True,
    })
    provider = AsyncMock(return_value=SimpleNamespace(success=False, model_dump=lambda: {"success": False}))
    monkeypatch.setattr(export_app_code.export_to_github_tool, "execute", provider)
    monkeypatch.setattr(export_app_code, "get_latest_workflow_export", AsyncMock(return_value=None))

    result = await export_app_code.export_app_code_to_github(
        app_id="customer-app", bundle_path=str(archive_path), context_variables=context,
    )

    if fault is None:
        assert result.get("blocked") is not True
        provider.assert_awaited_once()
    else:
        assert result["blocked"] is True
        provider.assert_not_awaited()
